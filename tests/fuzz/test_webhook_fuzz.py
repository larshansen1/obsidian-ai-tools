"""Property-based security fuzzing for the kai webhook server.

Runs in-process against ``create_app()`` with the ingest pipeline mocked, so a
fuzz run costs no LLM tokens, makes no network calls, and writes nothing
outside ``tmp_path``.

Excluded from the default suite by the ``fuzz`` marker; run with ``make fuzz``.

These are property tests, so assertions are stated as exact invariants over the
whole input space (an exact allowed status-code set, exact containment via
``Path.is_relative_to``) rather than the fixed-input equality used elsewhere.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from obsidian_ai_tools._vault_store import PathTraversalError, VaultStore
from obsidian_ai_tools.ingestion import ContentFetchError, ProviderSelectionError
from obsidian_ai_tools.obsidian import build_filename, sanitize_filename
from obsidian_ai_tools.server.app import create_app

from .payloads import (
    CORS_ORIGINS,
    FILENAME_ABUSE,
    LEAK_MARKERS,
    MALFORMED_BODIES,
    PATH_TRAVERSAL,
    SSRF_URLS,
)

pytestmark = pytest.mark.fuzz

# The endpoint maps exactly four outcomes. Anything else is a fuzz finding:
# 200 success, 400 no provider, 422 fetch failure or schema rejection,
# 500 note-generation or vault-write failure.
ALLOWED_INGEST_STATUS = frozenset({200, 400, 422, 500})
ALLOWED_LOOKUP_STATUS = frozenset({200, 422})

FUZZ_SETTINGS = settings(
    max_examples=300,
    deadline=None,  # the app is mocked; wall-clock jitter is not a finding
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def _client(tmp_path: Path) -> TestClient:
    """A test client whose settings point at an isolated sandbox vault.

    Matches the SimpleNamespace stub the rest of the suite uses, so no real
    Settings (and therefore no real credentials) are ever constructed here.
    """
    sandbox = tmp_path / "vault"
    sandbox.mkdir(exist_ok=True)
    stub = SimpleNamespace(
        obsidian_vault_path=sandbox,
        obsidian_inbox_folder="inbox",
        llm_model="test-model",
        openrouter_api_key="test-key",
        llm_base_url="https://openrouter.ai/api/v1",
        max_transcript_length=1234,
    )
    patcher = patch("obsidian_ai_tools.server.app.get_settings", return_value=stub)
    patcher.start()
    return TestClient(create_app(), raise_server_exceptions=False)


def _assert_no_leak(body: str) -> None:
    """An error body must never carry a traceback, an install path, or a key."""
    for marker in LEAK_MARKERS:
        assert marker not in body, f"error body leaked {marker!r}: {body[:400]}"


# ---------------------------------------------------------------------------
# HTTP surface: the endpoint must never crash open
# ---------------------------------------------------------------------------


class TestIngestRobustness:
    """/ingest must answer with a mapped status for any input, never a leak."""

    @FUZZ_SETTINGS
    @given(
        url=st.text(min_size=0, max_size=2000),
        prompt_version=st.one_of(st.none(), st.text(max_size=200)),
        transcript_providers=st.one_of(st.none(), st.text(max_size=200)),
        max_pages=st.one_of(st.none(), st.integers(min_value=-(2**63), max_value=2**63)),
        captured_title=st.one_of(st.none(), st.text(max_size=500)),
        captured_date=st.one_of(st.none(), st.text(max_size=200)),
        update=st.booleans(),
    )
    def test_arbitrary_fields_never_produce_unmapped_status(
        self,
        tmp_path: Path,
        url: str,
        prompt_version: str | None,
        transcript_providers: str | None,
        max_pages: int | None,
        captured_title: str | None,
        captured_date: str | None,
        update: bool,
    ) -> None:
        client = _client(tmp_path)
        with patch(
            "obsidian_ai_tools.server.app.ingest_content",
            side_effect=ProviderSelectionError("no provider"),
        ):
            response = client.post(
                "/ingest",
                json={
                    "url": url,
                    "prompt_version": prompt_version,
                    "transcript_providers": transcript_providers,
                    "max_pages": max_pages,
                    "captured_title": captured_title,
                    "captured_date": captured_date,
                    "update": update,
                },
            )

        assert response.status_code in ALLOWED_INGEST_STATUS
        _assert_no_leak(response.text)

    @pytest.mark.parametrize(
        "name,raw,schema_rejects", MALFORMED_BODIES, ids=[n for n, _, _ in MALFORMED_BODIES]
    )
    def test_malformed_body_is_rejected_without_leaking(
        self, tmp_path: Path, name: str, raw: str, schema_rejects: bool
    ) -> None:
        client = _client(tmp_path)
        with patch(
            "obsidian_ai_tools.server.app.ingest_content",
            side_effect=ProviderSelectionError("no provider"),
        ):
            response = client.post(
                "/ingest",
                content=raw.encode("utf-8", errors="surrogatepass"),
                headers={"content-type": "application/json"},
            )

        assert response.status_code in ALLOWED_INGEST_STATUS
        if schema_rejects:
            # Must stop at validation, never reach the pipeline.
            assert response.status_code == 422
        _assert_no_leak(response.text)

    @pytest.mark.parametrize("url", SSRF_URLS)
    def test_url_is_echoed_verbatim_into_the_service_layer(self, tmp_path: Path, url: str) -> None:
        """The webhook does no URL filtering of its own.

        This pins the trust boundary: every scheme, loopback address and
        encoded-IP form below reaches provider selection unchanged. If a
        scheme allowlist is ever added at the HTTP edge, this test fails and
        must be rewritten to assert the rejection instead.
        """
        client = _client(tmp_path)
        with patch(
            "obsidian_ai_tools.server.app.ingest_content",
            side_effect=ProviderSelectionError("no provider"),
        ) as mock_ingest:
            response = client.post("/ingest", json={"url": url})

        assert response.status_code == 400
        assert mock_ingest.call_args.args[0].url == url
        _assert_no_leak(response.text)

    @pytest.mark.parametrize("vault_path", PATH_TRAVERSAL)
    def test_caller_supplied_vault_path_reaches_the_service_layer(
        self, tmp_path: Path, vault_path: str
    ) -> None:
        """`vault_path` is attacker-controlled and unvalidated at the edge.

        Containment is therefore enforced solely by VaultStore. The companion
        test below fuzzes that check directly.
        """
        client = _client(tmp_path)
        with patch(
            "obsidian_ai_tools.server.app.ingest_content",
            side_effect=ContentFetchError("fetch failed"),
        ) as mock_ingest:
            response = client.post(
                "/ingest", json={"url": "http://127.0.0.1", "vault_path": vault_path}
            )

        assert response.status_code == 422
        assert mock_ingest.call_args.args[0].vault_path == Path(vault_path)
        _assert_no_leak(response.text)


class TestLookupRobustness:
    """/lookup is unauthenticated and reads the filesystem. It must not crash."""

    @FUZZ_SETTINGS
    @given(url=st.text(max_size=2000), vault_path=st.text(max_size=500))
    def test_lookup_never_produces_unmapped_status(
        self, tmp_path: Path, url: str, vault_path: str
    ) -> None:
        client = _client(tmp_path)
        with patch(
            "obsidian_ai_tools.server.app.find_note_by_source", return_value=None
        ) as mock_find:
            response = client.get("/lookup", params={"url": url, "vault_path": vault_path})

        assert response.status_code in ALLOWED_LOOKUP_STATUS
        assert mock_find.call_count == 1
        _assert_no_leak(response.text)


class TestCorsAllowlist:
    """Only chrome-extension:// origins may be reflected back to a browser."""

    @pytest.mark.parametrize("origin,should_allow", CORS_ORIGINS, ids=[o for o, _ in CORS_ORIGINS])
    def test_only_extension_origins_are_reflected(
        self, tmp_path: Path, origin: str, should_allow: bool
    ) -> None:
        client = _client(tmp_path)
        response = client.get("/status", headers={"origin": origin})
        allowed = response.headers.get("access-control-allow-origin")

        if should_allow:
            assert allowed == origin
        else:
            assert allowed is None

    def test_allowlist_admits_any_extension_id_not_just_the_kai_extension(
        self, tmp_path: Path
    ) -> None:
        """Documents the accepted width of the CORS rule.

        `chrome-extension://.*` trusts every extension installed in the
        browser, not only the kai one, because unpacked IDs differ per
        machine. There is no auth behind it, so any extension the user
        installs can drive /ingest. Change this test if the rule is narrowed
        to a pinned ID.
        """
        client = _client(tmp_path)
        unrelated = "chrome-extension://zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz"
        response = client.get("/status", headers={"origin": unrelated})

        assert response.headers.get("access-control-allow-origin") == unrelated


# ---------------------------------------------------------------------------
# Containment: the only thing standing between /ingest and an arbitrary write
# ---------------------------------------------------------------------------


class TestVaultContainment:
    """VaultStore.validate_path must reject every path outside the vault."""

    @FUZZ_SETTINGS
    @given(
        segments=st.lists(
            st.sampled_from(["..", ".", "a", "note", "inbox", "..\\", "...", " "]),
            min_size=1,
            max_size=6,
        )
    )
    def test_relative_segments_never_escape(self, tmp_path: Path, segments: list[str]) -> None:
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        store = VaultStore(vault)
        candidate = Path(*segments)

        try:
            resolved = store.resolve(candidate)
        except PathTraversalError:
            return

        assert resolved.is_relative_to(vault.resolve()), (
            f"escaped vault: {candidate} resolved to {resolved}"
        )

    def test_sibling_directory_sharing_a_name_prefix_is_rejected(self, tmp_path: Path) -> None:
        """A sibling whose name merely starts with the vault name is outside it.

        `startswith` on the string form accepts `/root/vault-evil` for a vault
        at `/root/vault`; `Path.is_relative_to` does not.
        """
        vault = tmp_path / "vault"
        vault.mkdir()
        sibling = tmp_path / "vault-evil"
        sibling.mkdir()
        store = VaultStore(vault)

        with pytest.raises(PathTraversalError):
            store.validate_path(sibling / "stolen.md")

    @pytest.mark.parametrize("hostile", PATH_TRAVERSAL)
    def test_corpus_paths_are_contained_or_rejected(self, tmp_path: Path, hostile: str) -> None:
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)
        store = VaultStore(vault)

        try:
            resolved = store.resolve(Path(hostile.replace("\x00", "")))
        except (PathTraversalError, ValueError, OSError):
            return

        assert resolved.is_relative_to(vault.resolve()), (
            f"escaped vault: {hostile} resolved to {resolved}"
        )


class TestFilenameSafety:
    """A hostile note title must not become a hostile filename."""

    @FUZZ_SETTINGS
    @given(title=st.text(max_size=300))
    def test_sanitized_name_is_always_a_single_safe_segment(self, title: str) -> None:
        name = sanitize_filename(title)

        assert "/" not in name
        assert "\\" not in name
        assert len(name) <= 100
        assert name != ""
        assert name not in {".", ".."}

    @pytest.mark.parametrize("title", FILENAME_ABUSE)
    def test_corpus_titles_stay_inside_the_inbox(self, tmp_path: Path, title: str) -> None:
        vault = tmp_path / "vault"
        inbox = vault / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)

        filename = build_filename("web", title)
        assert "/" not in filename
        assert "\\" not in filename
        assert "\x00" not in filename

        resolved = (inbox / filename).resolve()
        assert resolved.is_relative_to(inbox.resolve())
        assert resolved != inbox.resolve()
