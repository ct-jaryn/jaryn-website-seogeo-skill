"""Regression checks for observational denominators, provenance, and host matching."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import summarize_observations as metrics


def observation(run_id="run-1", **changes):
    row = {
        "schema_version": 1,
        "run_id": run_id,
        "timestamp": "2026-09-17T15:00:00+08:00",
        "prompt_id": "哪家适合我的业务？",
        "panel_id": "buying-intent-v1",
        "engine": "ChatGPT",
        "surface": "consumer-ui",
        "provider": "manual",
        "model": None,
        "locale": "zh-CN",
        "region": "CN",
        "search_enabled": True,
        "session_condition": "fresh-account-v1",
        "status": "success",
        "raw_answer": "这是有来源的回答。",
        "brand_mentioned": True,
        "brand_recommended": False,
        "cited_urls": ["https://docs.jaryn.example/source"],
    }
    row.update(changes)
    return row


def report(*rows):
    valid = [metrics.validate_row(row, index) for index, row in enumerate(rows, 1)]
    return metrics.summarize(valid, ["jaryn.example"])


class MetricsTests(unittest.TestCase):
    def test_failures_and_nonanswers_never_dilute_valid_rates(self):
        result = report(
            observation("answer"),
            observation("failure", status="error", brand_mentioned=False, raw_answer=None),
            observation("refusal", status="no_answer", brand_mentioned=False, raw_answer="无法回答"),
        )
        actual = result["groups"][0]["metrics"]
        self.assertEqual(actual["valid_answer_count"], 1)
        self.assertEqual(actual["error_count"], 1)
        self.assertEqual(actual["no_answer_count"], 1)
        self.assertEqual(actual["brand_mention_rate"], 1.0)
        self.assertEqual(actual["brand_recommendation_rate"], 0.0)
        self.assertEqual(actual["brand_domain_citation_rate"], 1.0)

    def test_unknown_measurements_have_their_own_denominators(self):
        result = report(
            observation("unknown", brand_mentioned=None, brand_recommended=None, cited_urls=None),
            observation("absent", brand_mentioned=False, brand_recommended=True, cited_urls=[]),
            observation("present"),
        )["groups"][0]["metrics"]
        self.assertEqual(result["mention_assessed_count"], 2)
        self.assertEqual(result["mention_unknown_count"], 1)
        self.assertEqual(result["brand_mention_rate"], 0.5)
        self.assertEqual(result["brand_recommendation_rate"], 0.5)
        self.assertEqual(result["citation_assessed_count"], 2)
        self.assertEqual(result["citation_unknown_count"], 1)
        self.assertEqual(result["brand_domain_citation_rate"], 0.5)

    def test_zero_valid_answers_and_empty_input_return_no_invented_rates(self):
        actual = report(observation(status="error", raw_answer=None))["groups"][0]["metrics"]
        for field in ("brand_mention_rate", "brand_recommendation_rate", "brand_domain_citation_rate"):
            self.assertIsNone(actual[field])
        self.assertEqual(report()["groups"], [])
        self.assertEqual(report()["totals"]["run_count"], 0)
        unknown = report(observation(brand_mentioned=None, brand_recommended=None, cited_urls=None))
        self.assertIsNone(unknown["groups"][0]["metrics"]["brand_mention_rate"])

    def test_host_boundaries_case_idna_and_duplicate_citations(self):
        impostors = [
            "https://jaryn.example.evil.test/page",
            "https://notjaryn.example/page",
            "https://evil.test/?source=https://jaryn.example",
        ]
        result = report(
            observation("impostor", cited_urls=impostors),
            observation("subdomain", cited_urls=["https://DOCS.JARYN.EXAMPLE./a", "https://jaryn.example/a"]),
        )["groups"][0]["metrics"]
        self.assertEqual(result["brand_domain_cited_answer_count"], 1)
        self.assertEqual(result["brand_domain_citation_rate"], 0.5)
        international = metrics.brand_domain("例子.中国")
        self.assertTrue(metrics.host_matches(metrics.citation_host("https://知识.例子.中国/资料"), [international]))
        self.assertFalse(metrics.host_matches(metrics.citation_host("https://假例子.中国/"), [international]))

    def test_backslashes_and_credentials_are_rejected_before_matching(self):
        ambiguous = [
            "https://evil.test\\@jaryn.example/source",
            "https://jaryn.example\\evil.test/source",
            "https://jaryn.example/path\\segment",
            "https://jaryn.example@evil.test/page",
            "https://user:secret@jaryn.example/page",
            "https://@jaryn.example/page",
        ]
        for url in ambiguous:
            with self.subTest(url=url), self.assertRaises(metrics.ObservationError) as caught:
                metrics.validate_row(observation(cited_urls=[url]), 9)
            self.assertEqual(caught.exception.line, 9)

    def test_lossy_unicode_host_mappings_fail_closed(self):
        ambiguous = [
            "faß.de", "FAẞ.de", "ος.example",  # sharp-s and Greek final sigma
            "ab\u200cc.example", "ab\u200dc.example",  # zero-width joiners
            "ab\u00adc.example", "ﬀ.example", "ＦＯＯ.example",  # deleted/folded labels
        ]
        for host in ambiguous:
            with self.subTest(host=host):
                with self.assertRaisesRegex(ValueError, "ASCII/Punycode"):
                    metrics.normalize_host(host)
                with self.assertRaises(metrics.ObservationError) as caught:
                    metrics.validate_row(observation(cited_urls=["https://" + host + "/source"]), 10)
                self.assertEqual(caught.exception.line, 10)

    def test_verified_punycode_does_not_collapse_to_legacy_mapping(self):
        sharp_s = metrics.brand_domain("xn--fa-hia.de")
        self.assertEqual(sharp_s, "xn--fa-hia.de")
        self.assertFalse(metrics.host_matches(metrics.citation_host("https://fass.de/a"), [sharp_s]))
        self.assertTrue(metrics.host_matches(metrics.citation_host("https://docs.xn--fa-hia.de/a"), [sharp_s]))
        # ος and οσ have different Punycode labels in nontransitional IDNA.
        final_sigma = "xn--" + "ος".encode("punycode").decode("ascii") + ".example"
        ordinary_sigma = "xn--" + "οσ".encode("punycode").decode("ascii") + ".example"
        self.assertNotEqual(final_sigma, ordinary_sigma)
        self.assertFalse(metrics.host_matches(metrics.citation_host("https://" + final_sigma), [ordinary_sigma]))

    def test_unambiguous_unicode_and_canonical_equivalents_still_match(self):
        chinese = metrics.brand_domain("例子。中国")
        self.assertEqual(chinese, metrics.brand_domain("例子.中国"))
        self.assertTrue(metrics.host_matches(metrics.citation_host("https://知识.例子.中国/资料"), [chinese]))
        self.assertEqual(metrics.normalize_host("cafe\u0301.example"), metrics.normalize_host("café.example"))
        self.assertEqual(metrics.normalize_host("οσ.example"), "xn--0xai.example")

    def test_changed_conditions_remain_separate_groups(self):
        variants = {
            "engine": "Gemini", "surface": "model-api", "provider": "test-api",
            "model": "known-model", "locale": "en-US", "region": "US",
            "panel_id": "buying-intent-v2", "search_enabled": False,
            "session_condition": "fresh-account-v2",
        }
        rows = [observation("base")]
        rows.extend(observation("different-" + field, **{field: value}) for field, value in variants.items())
        result = report(*rows)
        self.assertEqual(result["totals"]["group_count"], len(rows))
        self.assertNotIn("brand_mention_rate", result["totals"])
        base = next(group for group in result["groups"] if group["conditions"] == {
            field: rows[0][field] for field in metrics.CONDITION_FIELDS
        })
        self.assertEqual(base["unknown_conditions"], ["model"])

    def test_prompt_repetition_and_timezones_are_visible(self):
        result = report(
            observation("one", prompt_id="p1", timestamp="2026-09-17T09:00:00+08:00"),
            observation("two", prompt_id="p1", timestamp="2026-09-17T00:30:00Z"),
            observation("three", prompt_id="p2", status="error", raw_answer=None),
        )["groups"][0]
        self.assertEqual(result["prompt_run_counts"], {"p1": 2, "p2": 1})
        self.assertEqual(result["prompt_valid_answer_counts"], {"p1": 2, "p2": 0})
        self.assertEqual(result["first_observed_at"], "2026-09-17T00:30:00+00:00")

    def test_success_requires_answer_or_provenance(self):
        with self.assertRaisesRegex(metrics.ObservationError, "success requires"):
            metrics.validate_row(observation(raw_answer="  "), 4)
        valid = metrics.validate_row(observation(raw_answer=None, evidence_path="captures/run-1.json"), 1)
        self.assertEqual(valid["evidence_path"], "captures/run-1.json")

    def test_malformed_fields_report_the_input_line(self):
        examples = [
            {"brand_mentioned": 1}, {"brand_recommended": "yes"},
            {"status": "maybe"}, {"search_enabled": 0}, {"schema_version": True},
            {"timestamp": "2026-09-17T12:00:00"}, {"cited_urls": ["/relative"]},
            {"cited_urls": ["https://jaryn.example:99999/"]},
            {"cited_urls": ["https://jaryn.example/\n"]},
        ]
        for changes in examples:
            with self.subTest(changes=changes):
                with self.assertRaises(metrics.ObservationError) as caught:
                    metrics.validate_row(observation(**changes), 7)
                self.assertEqual(caught.exception.line, 7)
        missing = observation()
        del missing["cited_urls"]
        with self.assertRaisesRegex(metrics.ObservationError, "cited_urls is required"):
            metrics.validate_row(missing, 8)


class FileAndCliTests(unittest.TestCase):
    def test_duplicate_run_ids_are_rejected_across_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "observations.jsonl"
            target.write_text("\n" + json.dumps(observation()) + "\n" + json.dumps(observation(engine="Gemini")), encoding="utf-8")
            with self.assertRaises(metrics.ObservationError) as caught:
                metrics.read_observations(target)
            self.assertEqual(caught.exception.line, 3)
            self.assertIn("first seen on line 2", str(caught.exception))

    def test_bad_json_and_duplicate_keys_are_line_errors(self):
        for content in ("\n{broken", '{"run_id":"a","run_id":"b"}', '{"x":NaN}'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "bad.jsonl"
                target.write_text(content, encoding="utf-8")
                with self.assertRaises(metrics.ObservationError) as caught:
                    metrics.read_observations(target)
                self.assertIn("invalid JSON", str(caught.exception))
                self.assertIsNotNone(caught.exception.line)

    def test_cli_prints_unicode_json_creates_output_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "observations.jsonl"
            output = Path(directory) / "summary.json"
            source.write_text(json.dumps(observation(), ensure_ascii=False) + "\n", encoding="utf-8")
            args = [str(source), "--brand-domain", "jaryn.example", "--out", str(output)]
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(metrics.main(args), 0)
            self.assertEqual(json.loads(stdout.getvalue()), json.loads(output.read_text(encoding="utf-8")))
            self.assertIn("哪家适合我的业务", stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "")
            original = output.read_text(encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(metrics.main(args), 2)
            self.assertEqual(output.read_text(encoding="utf-8"), original)

    def test_cli_input_error_is_json_with_line_number(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.jsonl"
            source.write_text("\n[1,2,3]\n", encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(metrics.main([str(source), "--brand-domain", "jaryn.example"]), 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(json.loads(stderr.getvalue())["line"], 2)


if __name__ == "__main__":
    unittest.main()
