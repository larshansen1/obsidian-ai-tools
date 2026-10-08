"""Shared attack corpus for the webhook fuzzers.

One module so the in-process property fuzzer (tests/fuzz/test_webhook_fuzz.py)
and the black-box script (scripts/fuzz_webhook.py) probe the same inputs and
cannot drift apart.

Every payload here is inert: it exercises parsing and path handling, it does
not carry a working exploit. Loopback-only SSRF targets are used so a probe
that *does* get fetched hits nothing outside the machine under test.
"""

from __future__ import annotations

# Paths that must never resolve to a write outside the vault root.
# Includes the sibling-prefix case ("vault-evil" vs "vault"), which a
# str.startswith() containment check accepts and Path.is_relative_to() rejects.
PATH_TRAVERSAL: tuple[str, ...] = (
    "../../../../etc",
    "..\\..\\..\\windows\\system32",
    "/etc",
    "/tmp",  # nosec B108 - probe string, nothing is written here
    "~/.ssh",
    "vault/../../..",
    "vault/./../../etc",
    "....//....//etc",
    "%2e%2e%2f%2e%2e%2fetc",
    "..%252f..%252fetc",
    "/dev/null",
    "../../etc",
    "vault\x00/etc/passwd",
)

# Titles flow into build_filename() and become a real filename on disk.
FILENAME_ABUSE: tuple[str, ...] = (
    "../../../evil",
    "..",
    "...",
    ".hidden",
    "con",  # reserved device name on Windows
    "nul",
    "a" * 5000,
    "note\x00.md",
    "note\n\nrm -rf",
    "note\r\nX-Injected: 1",
    "‮exe.dcm",  # right-to-left override, filename spoofing
    "﻿bom-prefixed",
    "🔥" * 200,
    "",
    "   ",
    "-" * 200,
    "note/../../etc/passwd",
)

# URL values reaching provider selection and the content fetcher.
# All network-bearing entries point at loopback or link-local so nothing
# leaves the host if a fetch actually fires.
SSRF_URLS: tuple[str, ...] = (
    "file:///etc/passwd",
    "file://localhost/etc/shadow",
    "http://127.0.0.1:8765/status",
    "http://127.0.0.1:22",
    "http://[::1]:8765/status",
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata, link-local
    "http://metadata.google.internal/computeMetadata/v1/",
    "gopher://127.0.0.1:8765/_GET",
    "dict://127.0.0.1:11211/stat",
    "ftp://127.0.0.1/",
    "jar:http://127.0.0.1!/",
    "data:text/html,<script>1</script>",
    "javascript:1",
    "http://127.0.0.1@evil.invalid/",
    "http://evil.invalid#@127.0.0.1/",
    "HtTp://127.0.0.1:8765/status",
    "http://０.０.０.０/",  # fullwidth digits
    "http://2130706433/",  # decimal-encoded 127.0.0.1
)

# Structural abuse of the JSON body itself.
#
# Third element is `schema_rejects`: True when the body cannot satisfy the
# IngestRequest schema, so it is guaranteed to stop at validation. Only those
# are safe to send at a LIVE server without --allow-ingest; the False ones
# carry a valid `url` and would reach the fetcher and the LLM, spending money
# and writing a note.
MALFORMED_BODIES: tuple[tuple[str, str, bool], ...] = (
    ("empty", "", True),
    ("not-json", "not json at all", True),
    ("naked-array", "[]", True),
    ("naked-string", '"url"', True),
    ("null-body", "null", True),
    ("unclosed", '{"url": "http://127.0.0.1"', True),
    ("deep-nest", '{"url": ' + "[" * 400 + "]" * 400 + "}", True),
    ("no-url", '{"prompt_version": "v1"}', True),
    ("url-wrong-type", '{"url": 12345}', True),
    ("url-null", '{"url": null}', True),
    ("nan", '{"url": "http://127.0.0.1", "max_pages": NaN}', True),
    ("dup-keys", '{"url": "http://a.invalid", "url": "http://127.0.0.1"}', False),
    ("bad-scalar-types", '{"url": "http://127.0.0.1", "update": "yes", "max_pages": "lots"}', True),
    ("extra-keys", '{"url": "http://127.0.0.1", "__proto__": {"admin": true}}', False),
    ("big-int", '{"url": "http://127.0.0.1", "max_pages": ' + "9" * 400 + "}", False),
    ("neg-pages", '{"url": "http://127.0.0.1", "max_pages": -1}', False),
    ("null-byte", '{"url": "http://127.0.0.1\\u0000evil"}', False),
    (
        "unicode-bomb",
        '{"url": "http://127.0.0.1", "captured_title": "' + "\\ud83d\\udd25" * 500 + '"}',
        False,
    ),
)

# Origins probed against the chrome-extension:// CORS allowlist.
# The bool is what the app is *designed* to reflect back. The allowlist is
# deliberately any-extension (unpacked IDs differ per machine), so every
# well-formed chrome-extension:// origin is expected to be allowed; the
# width of that rule is asserted separately, not treated as a parse failure.
CORS_ORIGINS: tuple[tuple[str, bool], ...] = (
    ("chrome-extension://abcdefghijklmnopabcdefghijklmnop", True),
    ("https://evil.invalid", False),
    ("chrome-extension://abc.evil.invalid", True),
    ("https://evil.invalid/chrome-extension://abc", False),
    ("null", False),
    ("http://localhost:8765", False),
    ("moz-extension://abcdefgh", False),
    ("chrome-extension://abc\n.evil.invalid", False),
)

# Substrings that must never appear in a client-visible error body.
LEAK_MARKERS: tuple[str, ...] = (
    "Traceback (most recent call last)",
    "/site-packages/",
    "sk-",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    'File "/',
)
