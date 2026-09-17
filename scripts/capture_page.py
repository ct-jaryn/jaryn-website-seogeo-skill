#!/usr/bin/env python3
"""Capture one page's raw HTTP/HTML evidence, without SEO scoring or crawling."""

import argparse
import codecs
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from http.client import IncompleteRead
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_BYTES = 5 * 1024 * 1024
DEFAULT_UA = "JarynSEOAudit/1.0"
TEXT_BOUNDARIES = {"p", "div", "br", "li", "section", "table", "tr", "td", "th",
                   "main", "article", "h1", "h2", "h3", "h4", "h5", "h6"}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def validate_url(value):
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must be an absolute HTTP or HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Credentials in URLs are not supported")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("Control characters in URLs are not supported")
    parsed.port  # Reject malformed ports before making a request.
    return value


def resolve_reference(base, reference):
    if reference is None:
        return {"resolved_url": None}
    try:
        resolved = urljoin(base, reference)
        urlsplit(resolved)
        return {"resolved_url": resolved}
    except ValueError as exc:
        return {"resolved_url": None, "resolution_error": str(exc)}


def normalize_text(parts):
    return " ".join("".join(parts).split())


def attributes_evidence(attrs):
    return [{"name": name, "value": value} for name, value in attrs]


class PageParser(HTMLParser):
    """An indicative source parser. It neither renders nor proves visibility."""

    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.document_url = base_url
        self.lang = []
        self.titles = []
        self.meta = []
        self.canonicals = []
        self.alternates = []
        self.base = None
        self.headings = []
        self.primary_regions = []
        self.anchors = []
        self.jsonld = []
        self.active = []
        self.script = None
        self.suppressed = []
        self.template_depth = 0
        self.skipped_template_count = 0

    def handle_starttag(self, tag, attrs):
        if tag == "template":
            self.template_depth += 1
            self.skipped_template_count += 1
            return
        if self.template_depth:
            return
        # Keep the original attribute list; the convenience lookup takes the first.
        values = {}
        for name, value in attrs:
            values.setdefault(name, value)
        if tag == "html":
            self.lang.append(values.get("lang"))
        if tag == "base" and "href" in values and self.base is None:
            self.base = {"href": values["href"], **resolve_reference(self.document_url, values["href"])}
        if tag == "meta":
            self.meta.append({"attributes": attributes_evidence(attrs),
                              "name": values.get("name"), "content": values.get("content"),
                              "http_equiv": values.get("http-equiv"), "charset": values.get("charset")})
        if tag == "link":
            rels = (values.get("rel") or "").lower().split()
            item = {"href": values.get("href"), "attributes": attributes_evidence(attrs)}
            if "canonical" in rels:
                self.canonicals.append(dict(item))
            if "hreflang" in values:
                self.alternates.append({**item, "hreflang": values["hreflang"], "rel": rels})
        collection = None
        if tag == "title":
            collection = self.titles
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            collection = self.headings
        elif tag in {"main", "article"}:
            collection = self.primary_regions
        elif tag == "a":
            collection = self.anchors
        if collection is not None:
            item = {"tag": tag, "attributes": attributes_evidence(attrs), "_text": []}
            if tag == "a":
                item["href"] = values.get("href")
            collection.append(item)
            self.active.append(item)
        # Spaces prevent adjacent block elements from merging into one word.
        if tag in TEXT_BOUNDARIES:
            self.add_text(" ")
        if tag in {"script", "style"}:
            self.suppressed.append(tag)
        if tag == "script" and (values.get("type") or "").split(";")[0].strip().lower() == "application/ld+json":
            self.script = {"attributes": attributes_evidence(attrs), "_text": []}
            self.jsonld.append(self.script)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.template_depth:
            if tag == "template":
                self.template_depth -= 1
            return
        if tag == "script":
            self.script = None
        if tag in self.suppressed:
            self.suppressed.remove(tag)
        for index in range(len(self.active) - 1, -1, -1):
            if self.active[index]["tag"] == tag:
                self.active.pop(index)
                break
        if tag in TEXT_BOUNDARIES:
            self.add_text(" ")

    def add_text(self, data):
        if not self.suppressed and not self.template_depth:
            for item in self.active:
                item["_text"].append(data)

    def handle_data(self, data):
        if self.template_depth:
            return
        if self.script is not None:
            self.script["_text"].append(data)
        self.add_text(data)

    def result(self):
        effective_base = (self.base or {}).get("resolved_url") or self.document_url
        for collection in (self.titles, self.headings, self.primary_regions, self.anchors):
            for item in collection:
                item["text"] = normalize_text(item.pop("_text"))
        for item in self.canonicals + self.alternates + self.anchors:
            item.update(resolve_reference(effective_base, item.get("href")))
        for block in self.jsonld:
            block["text"] = "".join(block.pop("_text"))
            block["parse_error"] = None
            block["types"] = []
            try:
                def invalid_constant(value):
                    raise ValueError("Non-JSON numeric constant: " + value)
                value = json.loads(block["text"], parse_constant=invalid_constant)
                block["json_value_type"] = type(value).__name__
                pending = [value]
                while pending:
                    node = pending.pop()
                    if isinstance(node, dict):
                        types = node.get("@type", [])
                        if isinstance(types, str):
                            types = [types]
                        if isinstance(types, list):
                            for kind in types:
                                if isinstance(kind, str) and kind not in block["types"]:
                                    block["types"].append(kind)
                        pending.extend(reversed(list(node.values())))
                    elif isinstance(node, list):
                        pending.extend(reversed(node))
            except (ValueError, RecursionError) as exc:
                block["parse_error"] = str(exc)
        return {
            "evidence_kind": "source_html_not_browser_render",
            "limitations": [
                "No JavaScript execution, computed styles, layout, accessibility tree, or Google rendering.",
                "Text is indicative source text; hidden content may be included and visual content may be missing.",
                "HTMLParser is not a standards-complete browser DOM parser; malformed markup can differ.",
                "Template contents are omitted, including declarative shadow DOM templates; inspect rendered output separately.",
                "No robots.txt evaluation, indexing judgment, ranking score, or citation guarantee.",
                "JSON-LD parse success checks JSON syntax only, not Schema.org or rich-result eligibility.",
            ],
            "document_url": self.document_url, "first_base": self.base,
            "effective_base_url": effective_base, "html_lang_values": self.lang,
            "skipped_template_count": self.skipped_template_count,
            "titles": self.titles, "meta": self.meta,
            # Include all name/content declarations: arbitrary agent tokens are valid evidence.
            "meta_name_directives": [m for m in self.meta if m["name"] is not None],
            "meta_http_equiv": [m for m in self.meta if m["http_equiv"] is not None],
            "canonicals": self.canonicals, "hreflang_links": self.alternates,
            "headings": self.headings, "main_article_regions": self.primary_regions,
            "anchors": self.anchors, "jsonld_blocks": self.jsonld,
        }


class EvidenceRedirectHandler(HTTPRedirectHandler):
    def __init__(self, chain):
        super().__init__()
        self.chain = chain

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append({"requested_url": req.full_url, "status": code,
                           "location": headers.get("Location"), "resolved_location": newurl})
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def inspect_response_framing(headers, status, metadata):
    """Determine which length is meaningful before HTTPResponse mutates its state."""
    lengths = headers.get_all("Content-Length", [])
    transfers = headers.get_all("Transfer-Encoding", [])
    metadata["content_length_values"] = lengths
    metadata["transfer_encoding_values"] = transfers
    metadata["content_length_expected"] = None
    metadata["content_length_trusted"] = False

    def framing_error(kind, message):
        metadata["errors"].append({"phase": "response_framing", "type": kind, "message": message})

    # These responses have no message body. A 304 may legitimately describe the
    # representation length, which must not be compared with its empty body.
    if status in {204, 304} or 100 <= status < 200:
        metadata["body_framing"] = "no_body"
        return
    expected = None
    length_valid = True
    if lengths:
        tokens = [token.strip() for value in lengths for token in value.split(",")]
        try:
            if not tokens or any(not token or any(char not in "0123456789" for char in token) for token in tokens):
                raise ValueError("Content-Length is not an unsigned decimal integer")
            parsed = [int(token) for token in tokens]
            if len(set(parsed)) != 1:
                raise ValueError("Content-Length values disagree")
            expected = parsed[0]
        except ValueError as exc:
            length_valid = False
            framing_error("UntrustedContentLength", str(exc))
    if transfers:
        # urllib's HTTPResponse only decodes a single, exact chunked coding.
        # Never compare a Content-Length against a transfer-decoded chunked body.
        if lengths:
            framing_error("ConflictingBodyFraming", "Transfer-Encoding and Content-Length coexist; Content-Length is not trusted")
        if len(transfers) == 1 and transfers[0].strip().lower() == "chunked":
            metadata["body_framing"] = "chunked"
        else:
            metadata["body_framing"] = "untrusted"
            framing_error("UnsupportedTransferEncoding", "Only a single chunked transfer coding is supported")
    elif not length_valid:
        metadata["body_framing"] = "untrusted"
    elif expected is not None:
        metadata["body_framing"] = "content_length"
        metadata["content_length_expected"] = expected
        metadata["content_length_trusted"] = True
    else:
        metadata["body_framing"] = "connection_close"


def read_limited(stream, limit, metadata):
    parts = []
    count = 0
    eof_reached = False
    read_failed = False
    try:
        while count <= limit:
            chunk = stream.read(min(65536, limit + 1 - count))
            if not chunk:
                eof_reached = True
                break
            parts.append(chunk)
            count += len(chunk)
    except Exception as exc:
        read_failed = True
        if isinstance(exc, IncompleteRead) and exc.partial:
            parts.append(exc.partial[:max(0, limit + 1 - count)])
        metadata["errors"].append({"phase": "body_read", "type": type(exc).__name__, "message": str(exc)})
    body = b"".join(parts)
    metadata["truncated"] = len(body) > limit
    metadata["body_eof_reached"] = eof_reached
    metadata["body_bytes_read"] = len(body)
    # Null means the transfer could not be evaluated, for example because the
    # collector stopped at its byte cap or because conflicting headers were sent.
    metadata["transfer_incomplete"] = True if read_failed else None
    if eof_reached and metadata.get("body_framing") != "untrusted":
        expected = metadata.get("content_length_expected")
        mismatch = expected is not None and len(body) != expected
        metadata["transfer_incomplete"] = mismatch
        if mismatch:
            metadata["errors"].append({"phase": "body_read", "type": "ContentLengthMismatch",
                                       "message": "Expected {} body bytes; received {} before EOF".format(expected, len(body))})
    return body[:limit]


def parse_body(body, metadata, html_mode=False):
    if not html_mode and metadata.get("body_framing") == "untrusted":
        metadata["html_parse"] = "untrusted_body_framing"
        return None
    content_encoding = (metadata.get("content_encoding") or "identity").strip().lower()
    if content_encoding not in {"", "identity"}:
        metadata["html_parse"] = "unsupported_content_encoding"
        metadata["errors"].append({"phase": "decode", "type": "UnsupportedContentEncoding", "message": content_encoding})
        return None
    if not html_mode and metadata["content_type"] not in {"text/html", "application/xhtml+xml"}:
        metadata["html_parse"] = "not_html"
        return None
    encoding = metadata.get("declared_charset")
    encoding_source = "http_header" if encoding else None
    if not encoding:
        if body.startswith(codecs.BOM_UTF8):
            encoding, encoding_source = "utf-8-sig", "bom"
        elif body.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            encoding, encoding_source = "utf-16", "bom"
        else:
            # Parse, rather than regex-match, an ASCII-compatible source prefix for declarations.
            declaration_parser = PageParser(metadata["final_url"])
            declaration_parser.feed(body[:4096].decode("latin-1"))
            for meta in declaration_parser.meta:
                encoding = meta["charset"]
                if not encoding and (meta["http_equiv"] or "").lower() == "content-type":
                    from email.message import Message
                    message = Message()
                    message["content-type"] = meta["content"] or ""
                    encoding = message.get_content_charset()
                if encoding:
                    encoding_source = "html_declaration"
                    break
    encoding = encoding or "utf-8"
    metadata["decoding_encoding"] = encoding
    metadata["decoding_encoding_source"] = encoding_source or "utf8_fallback_not_browser_sniffing"
    try:
        text = body.decode(encoding, errors="strict")
    except LookupError as exc:
        metadata["html_parse"] = "unsupported_charset"
        metadata["errors"].append({"phase": "decode", "type": type(exc).__name__, "message": str(exc)})
        return None
    except UnicodeError as exc:
        metadata["errors"].append({"phase": "decode", "type": type(exc).__name__, "message": str(exc)})
        text = body.decode(encoding, errors="replace")
        metadata["decoding_replacements"] = True
    parser = PageParser(metadata["final_url"])
    try:
        parser.feed(text)
        parser.close()
        result = parser.result()
        result["source_truncated"] = metadata["truncated"]
        result["source_transfer_incomplete"] = metadata.get("transfer_incomplete")
        metadata["html_parse"] = "parsed_with_decode_errors" if metadata["decoding_replacements"] else "parsed"
        return result
    except Exception as exc:
        metadata["html_parse"] = "parser_error"
        metadata["errors"].append({"phase": "parse", "type": type(exc).__name__, "message": str(exc)})
        return None


def capture(args):
    target = args.url or args.base_url
    validate_url(target)
    output = Path(args.out_dir)
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    metadata = {
        "schema_version": 1, "started_at_utc": utc_now(), "finished_at_utc": None,
        "mode": "http" if args.url else "offline_html", "requested_url": target,
        "user_agent": args.user_agent if args.url else None, "request_accept_encoding": "identity" if args.url else None,
        "redirect_chain": [], "final_url": target, "status": None, "response_headers": [],
        "omitted_header_names": ["set-cookie"], "content_type": "text/html" if args.html_file else None,
        "content_encoding": None, "declared_charset": None, "truncated": False,
        "transfer_incomplete": None, "body_framing": "offline_file" if args.html_file else None,
        "body_bytes_note": "Stored bytes are after HTTP transfer decoding, without content-encoding decoding; not raw wire framing",
        "decoding_replacements": False, "max_bytes": args.max_bytes,
        "html_parse": "not_attempted", "errors": [],
        "timing_note": "total elapsed capture time, not TTFB", "body_file": "response-body.bin",
    }
    body = b""
    try:
        if args.html_file:
            metadata["html_file"] = str(Path(args.html_file).resolve())
            with open(args.html_file, "rb") as stream:
                body = read_limited(stream, args.max_bytes, metadata)
        else:
            opener = build_opener(EvidenceRedirectHandler(metadata["redirect_chain"]))
            request = Request(args.url, headers={"User-Agent": args.user_agent, "Accept-Encoding": "identity"}, method="GET")
            try:
                response = opener.open(request, timeout=args.timeout)
            except HTTPError as exc:
                response = exc  # A captured 404/500 is evidence, not an operational failure.
                if exc.code in {301, 302, 303, 307, 308} and "redirect" in str(exc.reason).lower():
                    metadata["errors"].append({"phase": "redirect", "type": "HTTPError", "message": str(exc.reason)})
            with response:
                metadata["status"] = response.code
                metadata["final_url"] = response.geturl()
                metadata["response_headers"] = [{"name": key, "value": value} for key, value in response.headers.items()
                                                if key.lower() != "set-cookie"]
                metadata["content_type"] = response.headers.get_content_type() if response.headers.get("Content-Type") else None
                encodings = response.headers.get_all("Content-Encoding", [])
                metadata["content_encoding"] = ", ".join(encodings) if encodings else None
                metadata["declared_charset"] = response.headers.get_content_charset()
                inspect_response_framing(response.headers, response.code, metadata)
                body = read_limited(response, args.max_bytes, metadata)
    except Exception as exc:
        metadata["errors"].append({"phase": "fetch" if args.url else "read_file", "type": type(exc).__name__, "message": str(exc)})
    metadata["capture_elapsed_seconds"] = round(time.monotonic() - started, 6)
    metadata["body_bytes_stored"] = len(body)
    metadata["sha256"] = hashlib.sha256(body).hexdigest()
    metadata["sha256_scope"] = "stored_response_body_bytes"
    (output / metadata["body_file"]).write_bytes(body)
    page = None
    if args.html_file or metadata["status"] is not None:
        page = parse_body(body, metadata, html_mode=bool(args.html_file))
    if page is not None:
        (output / "page.json").write_text(json.dumps(page, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    metadata["finished_at_utc"] = utc_now()
    metadata["operational_failure"] = bool(metadata["errors"]) or metadata["truncated"]
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"out_dir": str(output.resolve()), "status": metadata["status"],
                      "html_parse": metadata["html_parse"], "truncated": metadata["truncated"],
                      "transfer_incomplete": metadata["transfer_incomplete"],
                      "operational_failure": metadata["operational_failure"]}))
    return 2 if metadata["operational_failure"] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url")
    source.add_argument("--html-file")
    parser.add_argument("--base-url", help="Required document URL for offline HTML; not used for live URLs")
    parser.add_argument("--out-dir", required=True, help="New directory, which must not already exist")
    parser.add_argument("--user-agent", default=DEFAULT_UA)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--max-bytes", type=int, default=MAX_BYTES, help="Stored body cap; maximum 5 MiB")
    args = parser.parse_args(argv)
    if bool(args.html_file) != bool(args.base_url):
        parser.error("--base-url is required with --html-file and cannot be used with --url")
    if not 0 < args.max_bytes <= MAX_BYTES:
        parser.error("--max-bytes must be between 1 and 5242880")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    if not args.user_agent or any(ord(char) < 32 or ord(char) == 127 for char in args.user_agent):
        parser.error("--user-agent must be nonempty and contain no control characters")
    try:
        return capture(args)
    except (ValueError, OSError) as exc:
        print("capture_page: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
