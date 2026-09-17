#!/usr/bin/env python3
"""Deterministic fixtures and local HTTP tests; no external network access."""

import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPResponse, IncompleteRead
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("capture_page", Path(__file__).with_name("capture_page.py"))
CAPTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CAPTURE)


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/drop":
            self.close_connection = True
            return
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.send_header("X-Source", "redirect")
            self.end_headers()
            return
        if self.path == "/redirect-credentials":
            self.send_response(302)
            self.send_header("Location", "http://user:pass@127.0.0.1/final")
            self.end_headers()
            return
        status = 404 if self.path == "/missing" else 200
        self.send_response(status)
        self.send_header("Content-Type", "application/pdf" if self.path == "/pdf" else "text/html; charset=utf-8")
        self.send_header("X-Source", "final")
        self.send_header("X-Robots-Tag", "noindex")
        self.send_header("X-Robots-Tag", "bingbot: noarchive")
        self.send_header("Set-Cookie", "secret=do-not-store")
        body = b"<html><head><title>Final page</title></head><body><main>Actual content</main></body></html>"
        if self.path == "/gzip":
            body = gzip.compress(body)
            self.send_header("Content-Encoding", "gzip")
        elif self.path == "/unknown-encoding":
            self.send_header("Content-Encoding", "made-up")
        elif self.path == "/large":
            body = b"<html><main>" + b"x" * 1000 + b"</main></html>"
        elif self.path == "/pdf":
            body = b"%PDF-1.7 fake fixture"
        elif self.path == "/bad-utf8":
            body = b"<title>invalid \xff</title>"
        elif self.path == "/shortbody":
            body = b"<p>partial</p>"
        self.send_header("Content-Length", "500" if self.path == "/shortbody" else str(len(body)))
        self.send_header("X-Received-Accept-Encoding", self.headers.get("Accept-Encoding", ""))
        self.end_headers()
        self.wfile.write(body)


class CaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def run_capture(self, arguments, name="output"):
        output = self.directory / name
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = CAPTURE.main([*arguments, "--out-dir", str(output)])
        metadata = json.loads((output / "metadata.json").read_text()) if (output / "metadata.json").exists() else None
        page = json.loads((output / "page.json").read_text()) if (output / "page.json").exists() else None
        return code, metadata, page, output

    def test_offline_complete_evidence(self):
        fixture = self.directory / "fixture.html"
        fixture.write_text('''<!doctype html><html lang="zh-CN"><head>
          <title>First &amp; title</title><title>Second title</title>
          <link rel="canonical" href="first"><link rel="CANONICAL" href="second">
          <base href="/assets/"><base href="/ignored/">
          <meta name="robots" content="index"><meta name="robots" content="nosnippet">
          <meta name="unlisted-agent" content="noindex"><meta http-equiv="X-Robots-Tag" content="nofollow">
          <link rel="alternate" hreflang="en" href="../en/">
          </head><body><main><h1>Real <em>Heading</em></h1><p>A paragraph.</p>
          <article><h2>Detail</h2><p>Evidence</p></article><div hidden>Hidden source text</div>
          <script>not article content</script><a href="read">Read <b>more</b></a></main>
          <script type="application/ld+json">{"@graph":[{"@type":"Organization"},{"@type":["Article","Thing"]}]}</script>
          <script type="application/ld+json">{broken}</script></body></html>''', encoding="utf-8")
        with patch.object(CAPTURE, "build_opener", side_effect=AssertionError("Offline capture must not create a network opener")):
            code, metadata, page, output = self.run_capture(["--html-file", str(fixture), "--base-url", "https://example.test/path/page"])
        self.assertEqual(code, 0)
        self.assertIsNone(metadata["status"])
        self.assertEqual(page["html_lang_values"], ["zh-CN"])
        self.assertEqual([x["text"] for x in page["titles"]], ["First & title", "Second title"])
        self.assertEqual([x["resolved_url"] for x in page["canonicals"]], ["https://example.test/assets/first", "https://example.test/assets/second"])
        self.assertEqual(page["hreflang_links"][0]["resolved_url"], "https://example.test/en/")
        self.assertEqual(len(page["meta_name_directives"]), 3)
        self.assertEqual(len(page["meta_http_equiv"]), 1)
        self.assertEqual(page["headings"][0]["text"], "Real Heading")
        self.assertIn("Hidden source text", page["main_article_regions"][0]["text"])
        self.assertNotIn("not article content", page["main_article_regions"][0]["text"])
        self.assertEqual(page["anchors"][0]["resolved_url"], "https://example.test/assets/read")
        self.assertEqual(page["jsonld_blocks"][0]["types"], ["Organization", "Article", "Thing"])
        self.assertIsNotNone(page["jsonld_blocks"][1]["parse_error"])
        self.assertEqual(metadata["sha256"], hashlib.sha256((output / "response-body.bin").read_bytes()).hexdigest())

    def test_redirect_and_final_repeated_headers(self):
        code, metadata, page, _ = self.run_capture(["--url", self.origin + "/redirect"])
        self.assertEqual(code, 0)
        self.assertEqual(metadata["redirect_chain"], [{"requested_url": self.origin + "/redirect", "status": 302, "location": "/final", "resolved_location": self.origin + "/final"}])
        self.assertEqual(metadata["final_url"], self.origin + "/final")
        headers = metadata["response_headers"]
        self.assertEqual([h["value"] for h in headers if h["name"].lower() == "x-source"], ["final"])
        self.assertEqual([h["value"] for h in headers if h["name"].lower() == "x-robots-tag"], ["noindex", "bingbot: noarchive"])
        self.assertFalse(any(h["name"].lower() == "set-cookie" for h in headers))
        self.assertEqual([h["value"] for h in headers if h["name"].lower() == "x-received-accept-encoding"], ["identity"])
        self.assertEqual(page["titles"][0]["text"], "Final page")

    def test_404_is_captured_successfully(self):
        code, metadata, page, _ = self.run_capture(["--url", self.origin + "/missing"])
        self.assertEqual(code, 0)
        self.assertEqual(metadata["status"], 404)
        self.assertIsNotNone(page)

    def test_unsupported_content_encoding(self):
        for endpoint in ["gzip", "unknown-encoding"]:
            with self.subTest(endpoint=endpoint):
                code, metadata, page, output = self.run_capture(["--url", self.origin + "/" + endpoint], endpoint)
                self.assertEqual(code, 2)
                self.assertEqual(metadata["html_parse"], "unsupported_content_encoding")
                self.assertIsNone(page)
                self.assertGreater((output / "response-body.bin").stat().st_size, 0)

    def test_size_cap(self):
        code, metadata, page, output = self.run_capture(["--url", self.origin + "/large", "--max-bytes", "32"])
        self.assertEqual(code, 2)
        self.assertTrue(metadata["truncated"])
        self.assertEqual((output / "response-body.bin").stat().st_size, 32)
        self.assertTrue(page["source_truncated"])
        self.assertIsNone(metadata["transfer_incomplete"])

    def test_premature_eof_is_not_size_truncation_or_success(self):
        code, metadata, page, output = self.run_capture(["--url", self.origin + "/shortbody"])
        self.assertEqual(code, 2)
        self.assertTrue(metadata["transfer_incomplete"])
        self.assertFalse(metadata["truncated"])
        self.assertTrue(metadata["body_eof_reached"])
        self.assertEqual(metadata["content_length_expected"], 500)
        self.assertEqual((output / "response-body.bin").read_bytes(), b"<p>partial</p>")
        self.assertTrue(page["source_transfer_incomplete"])

    def test_template_metadata_is_inert_and_cannot_replace_real_base(self):
        fixture = self.directory / "template.html"
        fixture.write_text('''<html lang="en"><head><template>
          <base href="https://wrong.test/"><meta name="robots" content="noindex">
          <meta charset="not-a-codec"><link rel="canonical" href="wrong">
          <template><title>Nested inert</title><link rel="alternate" hreflang="fr" href="wrong-fr"></template>
          <script type="application/ld+json">{"@type":"Fake"}</script></template>
          <base href="/real/"><meta charset="utf-8"><meta name="robots" content="index">
          <link rel="canonical" href="page"><title>Actual</title></head>
          <body><main>Before<template><h1>Inert</h1><a href="wrong">Hidden</a><template>Nested</template></template>After</main>
          <h1>Actual heading</h1></body></html>''', encoding="utf-8")
        code, metadata, page, output = self.run_capture(["--html-file", str(fixture), "--base-url", "https://example.test/source"])
        self.assertEqual(code, 0)
        self.assertEqual(page["skipped_template_count"], 4)
        self.assertEqual(page["first_base"]["resolved_url"], "https://example.test/real/")
        self.assertEqual([item["resolved_url"] for item in page["canonicals"]], ["https://example.test/real/page"])
        self.assertEqual([item["content"] for item in page["meta_name_directives"]], ["index"])
        self.assertEqual([item["text"] for item in page["titles"]], ["Actual"])
        self.assertEqual([item["text"] for item in page["headings"]], ["Actual heading"])
        self.assertEqual(page["anchors"], [])
        self.assertEqual(page["hreflang_links"], [])
        self.assertEqual(page["jsonld_blocks"], [])
        self.assertEqual(page["main_article_regions"][0]["text"], "BeforeAfter")
        self.assertEqual((output / "response-body.bin").read_bytes(), fixture.read_bytes())

    def test_non_html_has_no_invented_page_checks(self):
        code, metadata, page, _ = self.run_capture(["--url", self.origin + "/pdf"])
        self.assertEqual(code, 0)
        self.assertEqual(metadata["html_parse"], "not_html")
        self.assertIsNone(page)

    def test_invalid_utf8_is_recorded(self):
        code, metadata, page, _ = self.run_capture(["--url", self.origin + "/bad-utf8"])
        self.assertEqual(code, 2)
        self.assertTrue(metadata["decoding_replacements"])
        self.assertIn("\ufffd", page["titles"][0]["text"])

    def test_network_failure_is_preserved(self):
        code, metadata, page, output = self.run_capture(["--url", self.origin + "/drop"])
        self.assertEqual(code, 2)
        self.assertEqual(metadata["errors"][0]["phase"], "fetch")
        self.assertIsNone(metadata["status"])
        self.assertIsNone(page)
        self.assertEqual((output / "response-body.bin").read_bytes(), b"")

    def test_credentials_rejected_before_output_created(self):
        code, metadata, _, output = self.run_capture(["--url", "https://user:pass@example.test/"])
        self.assertEqual(code, 2)
        self.assertFalse(output.exists())
        self.assertIsNone(metadata)

    def test_redirect_credentials_are_rejected(self):
        code, metadata, _, _ = self.run_capture(["--url", self.origin + "/redirect-credentials"])
        self.assertEqual(code, 2)
        self.assertTrue(metadata["operational_failure"])
        self.assertIsNone(metadata["status"])

    def test_existing_directory_refused(self):
        output = self.directory / "output"
        output.mkdir()
        marker = output / "marker.txt"
        marker.write_text("preserve")
        code, metadata, _, _ = self.run_capture(["--url", self.origin + "/final"])
        self.assertEqual(code, 2)
        self.assertIsNone(metadata)
        self.assertEqual(marker.read_text(), "preserve")

    def test_incomplete_read_keeps_partial_bytes(self):
        class BrokenStream:
            def read(self, _size):
                raise IncompleteRead(b"partial", 100)
        metadata = {"errors": [], "truncated": False}
        self.assertEqual(CAPTURE.read_limited(BrokenStream(), 32, metadata), b"partial")
        self.assertEqual(metadata["errors"][0]["type"], "IncompleteRead")
        self.assertTrue(metadata["transfer_incomplete"])

    def test_unknown_charset_preserves_raw_evidence(self):
        fixture = self.directory / "unknown.html"
        fixture.write_bytes(b'<meta charset="not-a-codec"><title>content</title>')
        code, metadata, page, output = self.run_capture(["--html-file", str(fixture), "--base-url", "https://example.test/"])
        self.assertEqual(code, 2)
        self.assertEqual(metadata["html_parse"], "unsupported_charset")
        self.assertIsNone(page)
        self.assertEqual((output / "response-body.bin").read_bytes(), fixture.read_bytes())

    def test_offline_declared_charset(self):
        fixture = self.directory / "latin.html"
        fixture.write_bytes(b'<meta charset="windows-1252"><title>Caf\xe9</title>')
        code, metadata, page, _ = self.run_capture(["--html-file", str(fixture), "--base-url", "https://example.test/"])
        self.assertEqual(code, 0)
        self.assertEqual(metadata["decoding_encoding_source"], "html_declaration")
        self.assertEqual(page["titles"][0]["text"], "Caf\u00e9")


class HTTPFramingTests(unittest.TestCase):
    def read_response(self, headers, body, limit=1024, status="200 OK"):
        class ByteSocket:
            def makefile(self, _mode):
                return io.BytesIO(b"HTTP/1.1 " + status.encode("ascii") + b"\r\n" + headers + b"\r\n" + body)
        response = HTTPResponse(ByteSocket())
        response.begin()
        metadata = {"errors": [], "truncated": False}
        CAPTURE.inspect_response_framing(response.headers, response.status, metadata)
        with response:
            stored = CAPTURE.read_limited(response, limit, metadata)
        return stored, metadata

    def test_real_httpresponse_short_fixed_length_without_exception(self):
        body, metadata = self.read_response(b"Content-Length: 500\r\n", b"<p>partial</p>")
        self.assertEqual(body, b"<p>partial</p>")
        self.assertTrue(metadata["transfer_incomplete"])
        self.assertFalse(metadata["truncated"])
        self.assertEqual(metadata["errors"][0]["type"], "ContentLengthMismatch")

    def test_complete_fixed_length_at_cap_is_success(self):
        body, metadata = self.read_response(b"Content-Length: 4\r\n", b"test", limit=4)
        self.assertEqual(body, b"test")
        self.assertFalse(metadata["transfer_incomplete"])
        self.assertFalse(metadata["truncated"])
        self.assertEqual(metadata["errors"], [])

    def test_chunked_body_does_not_use_content_length(self):
        for extra in [b"", b"Content-Length: 500\r\n"]:
            with self.subTest(extra=extra):
                body, metadata = self.read_response(b"Transfer-Encoding: chunked\r\n" + extra, b"4\r\ntest\r\n0\r\n\r\n")
                self.assertEqual(body, b"test")
                self.assertFalse(metadata["transfer_incomplete"])
                self.assertFalse(metadata["content_length_trusted"])
                self.assertIsNone(metadata["content_length_expected"])
                self.assertEqual([item["type"] for item in metadata["errors"]], ["ConflictingBodyFraming"] if extra else [])

    def test_conflicting_or_invalid_lengths_are_explicitly_untrusted(self):
        for headers in [b"Content-Length: 4\r\nContent-Length: 500\r\n", b"Content-Length: 4, 500\r\n", b"Content-Length: invalid\r\n"]:
            with self.subTest(headers=headers):
                _, metadata = self.read_response(headers, b"test")
                self.assertEqual(metadata["body_framing"], "untrusted")
                self.assertFalse(metadata["content_length_trusted"])
                self.assertIsNone(metadata["transfer_incomplete"])
                self.assertEqual(metadata["errors"][0]["type"], "UntrustedContentLength")

    def test_incomplete_chunked_transfer_keeps_decoded_partial_body(self):
        body, metadata = self.read_response(b"Transfer-Encoding: chunked\r\n", b"4\r\ntest\r\n")
        self.assertEqual(body, b"test")
        self.assertTrue(metadata["transfer_incomplete"])
        self.assertFalse(metadata["truncated"])
        self.assertEqual(metadata["errors"][0]["type"], "IncompleteRead")

    def test_identical_repeated_lengths_are_preserved_and_consistent(self):
        body, metadata = self.read_response(b"Content-Length: 4\r\nContent-Length: 4\r\n", b"test")
        self.assertEqual(body, b"test")
        self.assertEqual(metadata["content_length_values"], ["4", "4"])
        self.assertTrue(metadata["content_length_trusted"])
        self.assertFalse(metadata["transfer_incomplete"])

    def test_304_representation_length_is_not_body_length(self):
        body, metadata = self.read_response(b"Content-Length: 500\r\n", b"", status="304 Not Modified")
        self.assertEqual(body, b"")
        self.assertEqual(metadata["body_framing"], "no_body")
        self.assertFalse(metadata["transfer_incomplete"])
        self.assertEqual(metadata["errors"], [])

    def test_unsupported_transfer_coding_is_not_treated_as_valid_html(self):
        _, metadata = self.read_response(b"Transfer-Encoding: gzip, chunked\r\n", b"wire bytes")
        self.assertEqual(metadata["body_framing"], "untrusted")
        self.assertEqual(metadata["errors"][0]["type"], "UnsupportedTransferEncoding")
        self.assertIsNone(CAPTURE.parse_body(b"wire bytes", metadata))
        self.assertEqual(metadata["html_parse"], "untrusted_body_framing")


if __name__ == "__main__":
    unittest.main()
