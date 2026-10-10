#!/usr/bin/env python3
"""Summarize an explicitly assessed AI-answer observation panel, locally.

Usage:
  python3 summarize_observations.py observations.jsonl --brand-domain example.com
  python3 summarize_observations.py observations.jsonl --brand-domain example.com \
      --brand-domain example.org --out summary.json

Each nonblank input line must be a JSON object with this schema (version 1):
  schema_version: 1
  run_id: nonempty string, unique across the entire file
  timestamp: ISO-8601 timestamp with an explicit timezone
  prompt_id, panel_id: nonempty strings; panel_id identifies a versioned prompt set
  engine, surface, provider: nonempty strings, e.g. ChatGPT / consumer-ui / manual
  model, locale, region: nonempty strings or null when unknown
  search_enabled: true, false, or null when unknown
  session_condition: nonempty versioned condition identifier, e.g. fresh-us-v1;
      document account/login/history/personalization settings in the source panel
  status: "success", "error", or "no_answer" (includes refusals/nonanswers)
  brand_mentioned, brand_recommended: independently assessed boolean or null
  cited_urls: array of absolute HTTP(S) URLs, or null if citation capture is unknown
  raw_answer: string or null (optional except as success evidence)
  evidence_path: nonempty artifact reference or null (optional except as evidence)

A success needs a nonblank raw_answer OR evidence_path. Artifact references are
provenance assertions: this summarizer does not open them or verify their content.
The caller must classify an actual answer as success, refusals as no_answer, and
failed collection as error. Non-success assessments never enter rate denominators.
Use cited_urls=[] for verified absence of citations, null for unknown capture.
Unknown assessment fields must be explicit null, not omitted. Additional fields
(query text, collection notes, evidence hashes, errors) are allowed and ignored.

Citation URLs cannot contain backslashes or credentials. Host matching accepts
ASCII/Punycode and Unicode labels that survive a conservative stdlib IDNA round
trip after lowercasing/NFC normalization. Mapping-sensitive Unicode labels (e.g.
sharp-s, final sigma, joiners) are rejected: supply a browser-verified ASCII/
Punycode hostname instead. Ordinary Chinese labels remain supported. This is not
a complete IDNA2008/UTS46 or browser URL implementation; no hostname is resolved.
IPvFuture IP-literals are not supported and are explicitly rejected.

Rates are fractions in [0, 1], or null for an empty denominator. They describe
unweighted runs within each exact comparable-condition group, not market share,
causal uplift, source correctness, or citation support. Repeated prompts have more
influence; inspect prompt_run_counts before comparing runs or panels. Unknown
conditions remain null and are flagged; matching unknowns do not prove comparable
conditions. There is deliberately no pooled cross-engine rate or GEO score.

No networking, model calls, automatic mention/sentiment inference, or installation.
The input is never changed. --out exclusively creates a new report (no overwrite).
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import re
import sys
import unicodedata
from urllib.parse import urlsplit


CONDITION_FIELDS = (
    "engine", "surface", "provider", "model", "locale", "region", "panel_id",
    "search_enabled", "session_condition",
)
REQUIRED_STRINGS = (
    "run_id", "prompt_id", "panel_id", "engine", "surface", "provider",
    "session_condition",
)
NULLABLE_STRINGS = ("model", "locale", "region")
ASSESSMENTS = ("brand_mentioned", "brand_recommended")


class ObservationError(ValueError):
    """An input observation is not safe to aggregate."""

    def __init__(self, message, line=None):
        super().__init__(message)
        self.line = line


def normalize_host(value):
    """Normalize hosts without silently accepting lossy legacy IDNA mappings."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("hostname must be a nonblank string without whitespace")
    # IDNA recognizes these common dot equivalents as label separators.
    host = unicodedata.normalize("NFC", value.lower()).translate(
        str.maketrans({"\u3002": ".", "\uff0e": ".", "\uff61": "."})
    )
    if host.endswith("."):
        host = host[:-1]
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        pass
    labels = []
    for label in host.split("."):
        if label.isascii():
            # In particular, do not decode/re-encode verified IDNA2008 A-labels
            # through Python's older codec (xn--fa-hia must not become fass).
            labels.append(label)
            continue
        try:
            encoded = label.encode("idna").decode("ascii")
            round_trip = encoded.encode("ascii").decode("idna")
        except UnicodeError as exc:
            raise ValueError("unsupported Unicode hostname; use browser-verified ASCII/Punycode") from exc
        if round_trip != label:
            raise ValueError("ambiguous Unicode hostname mapping; use browser-verified ASCII/Punycode")
        labels.append(encoded)
    host = ".".join(labels)
    if len(host) > 253 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in host.split(".")
    ):
        raise ValueError("invalid hostname")
    return host


def brand_domain(value):
    """argparse adapter: brand domains must be hosts, not URLs or suffix patterns."""
    if any(char in value for char in "/:@?#*\\"):
        raise argparse.ArgumentTypeError("use a DNS hostname, not a URL or wildcard")
    try:
        host = normalize_host(value)
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return host
        raise ValueError("brand-domain must be a DNS hostname, not an IP address")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def citation_host(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("citation must be a nonblank absolute HTTP(S) URL")
    if any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError("citation URL contains unescaped whitespace/control characters")
    if "\\" in value:
        raise ValueError("citation URL contains a backslash; provide a verified browser URL")
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise ValueError("citation must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("citation URLs with credentials are not supported")
    _ = parsed.port  # Reject malformed or out-of-range ports.
    # urlsplit strips the brackets from IPvFuture literals as well as IPv6.
    if "[" in parsed.netloc and parsed.hostname.lower().startswith("v"):
        raise ValueError("citation URLs with IPvFuture IP-literals are not supported")
    return normalize_host(parsed.hostname)


def host_matches(host, domains):
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def parse_timestamp(value):
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("timestamp must be ISO-8601 with time and timezone")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601 with time and timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    try:
        return parsed.astimezone(timezone.utc).isoformat()
    except OverflowError as exc:
        raise ValueError("timestamp is outside the supported UTC datetime range") from exc


def validate_row(row, line):
    def fail(message):
        raise ObservationError(message, line)

    if not isinstance(row, dict):
        fail("each line must be a JSON object")
    if type(row.get("schema_version")) is not int or row["schema_version"] != 1:
        fail("schema_version must be integer 1")
    for field in REQUIRED_STRINGS:
        value = row.get(field)
        if not isinstance(value, str) or not value.strip():
            fail(field + " must be a nonblank string")
    for field in NULLABLE_STRINGS:
        if field not in row:
            fail(field + " is required; use null when unknown")
        value = row[field]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            fail(field + " must be a nonblank string or null")
    for field in ("search_enabled",) + ASSESSMENTS:
        if field not in row or (row[field] is not None and type(row[field]) is not bool):
            fail(field + " must be an explicit boolean or null")
    if row.get("status") not in ("success", "error", "no_answer"):
        fail("status must be success, error, or no_answer")
    try:
        timestamp = parse_timestamp(row.get("timestamp"))
    except ValueError as exc:
        fail(str(exc))
    for field in ("raw_answer", "evidence_path"):
        value = row.get(field)
        if value is not None and not isinstance(value, str):
            fail(field + " must be a string or null")
    if row["status"] == "success" and not any(
        (row.get(field) or "").strip() for field in ("raw_answer", "evidence_path")
    ):
        fail("success requires a nonblank raw_answer or evidence_path")
    if "cited_urls" not in row:
        fail("cited_urls is required; use null when unknown")
    urls = row["cited_urls"]
    hosts = None
    if urls is not None:
        if not isinstance(urls, list):
            fail("cited_urls must be an array or null")
        hosts = set()
        for index, url in enumerate(urls):
            try:
                hosts.add(citation_host(url))
            except (ValueError, UnicodeError) as exc:
                fail("cited_urls[{}]: {}".format(index, exc))
    return dict(row, _timestamp_utc=timestamp, _citation_hosts=hosts)


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: " + key)
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("nonstandard JSON constant: " + value)


def read_observations(path):
    rows = []
    seen = {}
    with Path(path).open(encoding="utf-8") as handle:
        for line, text in enumerate(handle, 1):
            if not text.strip():
                continue
            try:
                row = json.loads(text, object_pairs_hook=unique_keys, parse_constant=reject_constant)
            except ValueError as exc:
                raise ObservationError("invalid JSON: " + str(exc), line) from exc
            row = validate_row(row, line)
            if row["run_id"] in seen:
                raise ObservationError(
                    "duplicate run_id {!r}; first seen on line {}".format(row["run_id"], seen[row["run_id"]]),
                    line,
                )
            seen[row["run_id"]] = line
            rows.append(row)
    return rows


def rate(numerator, denominator):
    return numerator / denominator if denominator else None


def group_metrics(rows, domains):
    valid = [row for row in rows if row["status"] == "success"]
    statuses = Counter(row["status"] for row in rows)
    mentions = [row["brand_mentioned"] for row in valid if row["brand_mentioned"] is not None]
    recommendations = [row["brand_recommended"] for row in valid if row["brand_recommended"] is not None]
    cited = [row for row in valid if row["_citation_hosts"] is not None]
    cited_brand = sum(any(host_matches(host, domains) for host in row["_citation_hosts"]) for row in cited)
    return {
        "run_count": len(rows),
        "valid_answer_count": len(valid),
        "error_count": statuses["error"],
        "no_answer_count": statuses["no_answer"],
        "mention_assessed_count": len(mentions),
        "mention_unknown_count": len(valid) - len(mentions),
        "brand_mentioned_count": sum(mentions),
        "brand_mention_rate": rate(sum(mentions), len(mentions)),
        "recommendation_assessed_count": len(recommendations),
        "recommendation_unknown_count": len(valid) - len(recommendations),
        "brand_recommended_count": sum(recommendations),
        "brand_recommendation_rate": rate(sum(recommendations), len(recommendations)),
        "citation_assessed_count": len(cited),
        "citation_unknown_count": len(valid) - len(cited),
        "brand_domain_cited_answer_count": cited_brand,
        "brand_domain_citation_rate": rate(cited_brand, len(cited)),
    }


def summarize(rows, domains):
    grouped = {}
    for row in rows:
        key = tuple(row[field] for field in CONDITION_FIELDS)
        grouped.setdefault(key, []).append(row)
    groups = []
    for key, members in sorted(grouped.items(), key=lambda item: json.dumps(item[0], ensure_ascii=False)):
        conditions = dict(zip(CONDITION_FIELDS, key))
        prompts = Counter(row["prompt_id"] for row in members)
        valid_prompts = Counter(row["prompt_id"] for row in members if row["status"] == "success")
        groups.append({
            "conditions": conditions,
            "unknown_conditions": [field for field, value in conditions.items() if value is None],
            "first_observed_at": min(row["_timestamp_utc"] for row in members),
            "last_observed_at": max(row["_timestamp_utc"] for row in members),
            "unique_prompt_count": len(prompts),
            "prompt_run_counts": dict(sorted(prompts.items())),
            "prompt_valid_answer_counts": {prompt: valid_prompts[prompt] for prompt in sorted(prompts)},
            "metrics": group_metrics(members, domains),
        })
    statuses = Counter(row["status"] for row in rows)
    return {
        "schema_version": 1,
        "brand_domains": sorted(set(domains)),
        "methodology": {
            "unit": "one unique run_id; unweighted run-level rates within exact condition groups",
            "rate_scale": "0..1; null means an empty denominator",
            "denominators": {
                "brand_mention_rate": "successful answers with an explicit brand_mentioned boolean",
                "brand_recommendation_rate": "successful answers with an explicit brand_recommended boolean",
                "brand_domain_citation_rate": "successful answers with known cited_urls, including empty arrays",
            },
            "citation_match": "exact hostname or dot-delimited subdomain after conservative, lossless IDNA normalization",
            "limits": [
                "Input success and assessments are caller assertions, not independently verified judgments.",
                "evidence_path references are not opened or validated by this script.",
                "Null conditions do not establish comparability; inspect unknown_conditions.",
                "Unequal prompt repetitions affect run-level rates; inspect prompt_run_counts.",
                "Mapping-sensitive Unicode hosts require browser-verified ASCII/Punycode; no full UTS46 implementation.",
                "Citation presence does not establish recommendation, factual accuracy, or claim support.",
                "These observations are not market share, causal uplift, or a universal GEO score.",
            ],
        },
        "totals": {
            "run_count": len(rows), "valid_answer_count": statuses["success"],
            "error_count": statuses["error"], "no_answer_count": statuses["no_answer"],
            "group_count": len(groups),
        },
        "groups": groups,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="UTF-8 JSONL observations")
    parser.add_argument("--brand-domain", action="append", required=True, type=brand_domain,
                        help="owned hostname; repeat for multiple hosts (includes its subdomains)")
    parser.add_argument("--out", type=Path, help="also create this JSON file; refuses to overwrite")
    args = parser.parse_args(argv)
    try:
        result = summarize(read_observations(args.input), args.brand_domain)
        output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.out:
            with args.out.open("x", encoding="utf-8") as handle:
                handle.write(output)
        sys.stdout.write(output)
        return 0
    except (ObservationError, OSError, UnicodeError) as exc:
        error = {"error": str(exc)}
        if isinstance(exc, ObservationError) and exc.line is not None:
            error["line"] = exc.line
        sys.stderr.write(json.dumps(error, ensure_ascii=False) + "\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
