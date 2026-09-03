"""Tests for the `kai usage` command in commands/vault.py.

The command is a reporting shell over two DuckDB queries, so these tests seed a
real ObservabilityDB in tmp_path and drive the command through CliRunner rather
than mocking the DB layer - the same convention as
tests/test_cli.py::test_rebuild_index_command and tests/test_observability.py.

Note on counts: `usage` is wrapped in @track_command("usage"), which records its
own invocation *after* the report is rendered, so it never appears in its own
output.

Also covers command registration and the batch-move summary rendering from the
same module (the dry-run/confirm report that process-inbox prints), because
both are cheap unit-level checks of vault-local code.
"""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import call, patch

import pytest
import typer
from typer.testing import CliRunner

from obsidian_ai_tools.cli import app
from obsidian_ai_tools.commands import vault as vault_cmd
from obsidian_ai_tools.folder_organizer import NoteToMove
from obsidian_ai_tools.observability import ObservabilityDB, _set_db_for_test

runner = CliRunner()

# Every command name the report knows about when --all is passed.
KNOWN_COMMANDS = [
    "ingest",
    "preview",
    "rebuild-index",
    "process-inbox",
    "usage",
    "search",
    "serve:ingest",
]


@pytest.fixture
def usage_db(tmp_path: Path) -> Iterator[ObservabilityDB]:
    """A real, empty observability DB wired into get_db() for one test."""
    db = ObservabilityDB(tmp_path / "usage_obs.duckdb")
    _set_db_for_test(db)
    yield db
    _set_db_for_test(None)


def _row_for(output: str, command: str) -> str:
    """Return the report line for a command, or fail with the whole table."""
    for line in output.splitlines():
        if line.startswith(command + " ") or line == command:
            return line
    raise AssertionError(f"No row for {command!r} in:\n{output}")


def test_usage_renders_the_invocation_table(usage_db: ObservabilityDB) -> None:
    """Recorded invocations show up with call counts and a success rate."""
    usage_db.record_invocation("ingest", "success", 1.2)
    usage_db.record_invocation("ingest", "success", 0.8)
    usage_db.record_invocation("ingest", "error", 0.5, error_type="ContentFetchError")
    usage_db.record_invocation("search", "success", 0.3)

    result = runner.invoke(app, ["usage"])

    assert result.exit_code == 0
    assert "Command usage — last 30 day(s)" in result.output
    assert "No command invocations recorded yet." not in result.output

    ingest_fields = _row_for(result.output, "ingest").split()
    assert ingest_fields[:3] == ["ingest", "3", "67%"]

    search_fields = _row_for(result.output, "search").split()
    assert search_fields[:3] == ["search", "1", "100%"]


def test_usage_days_option_narrows_both_queries(usage_db: ObservabilityDB) -> None:
    """--days is forwarded to the invocation and provider summaries alike."""
    usage_db.record_invocation("ingest", "success", 1.0)
    usage_db.record_provider_attempt("web", "primary", "success", 0.4, url="https://example.com")

    with (
        patch.object(
            usage_db, "get_invocation_summary", wraps=usage_db.get_invocation_summary
        ) as invocation_summary,
        patch.object(
            usage_db, "get_provider_summary", wraps=usage_db.get_provider_summary
        ) as provider_summary,
    ):
        result = runner.invoke(app, ["usage", "--days", "7"])

    assert result.exit_code == 0
    assert invocation_summary.call_args == call(days=7)
    assert provider_summary.call_args == call(days=7)
    assert "Command usage — last 7 day(s)" in result.output
    assert "Provider attempts — last 7 day(s)" in result.output


def test_usage_on_an_empty_db_points_at_the_all_flag(usage_db: ObservabilityDB) -> None:
    """With nothing recorded and no --all, the guidance suggests --all."""
    result = runner.invoke(app, ["usage"])

    assert result.exit_code == 0
    assert "No command invocations recorded yet." in result.output
    assert "Run some commands, then check back — or use --all to see all commands." in result.output
    assert "never used" not in result.output


def test_usage_all_on_an_empty_db_lists_every_known_command(usage_db: ObservabilityDB) -> None:
    """--all turns the empty report into a full never-run inventory."""
    result = runner.invoke(app, ["usage", "--all"])

    assert result.exit_code == 0
    assert "No command invocations recorded yet." in result.output
    assert "(All commands shown below have never been run)" in result.output

    for command in KNOWN_COMMANDS:
        fields = _row_for(result.output, command).split()
        assert fields[:4] == [command, "0", "—", "never"]
        assert "<- never used" in _row_for(result.output, command)


def test_usage_all_keeps_recorded_commands_out_of_the_never_run_list(
    usage_db: ObservabilityDB,
) -> None:
    """A command that has run is reported once, with its real numbers."""
    usage_db.record_invocation("ingest", "success", 1.0)

    result = runner.invoke(app, ["usage", "--all"])

    assert result.exit_code == 0
    ingest_rows = [line for line in result.output.splitlines() if line.startswith("ingest ")]
    assert len(ingest_rows) == 1
    assert ingest_rows[0].split()[:3] == ["ingest", "1", "100%"]
    assert "never used" not in ingest_rows[0]
    assert "<- never used" in _row_for(result.output, "search")


def test_usage_renders_the_provider_table_with_a_fallback_rate(
    usage_db: ObservabilityDB,
) -> None:
    """The fallback rate is a share of that provider's total attempts."""
    for _ in range(3):
        usage_db.record_provider_attempt(
            "youtube", "primary", "success", 0.5, url="https://youtu.be/abc"
        )
    usage_db.record_provider_attempt(
        "youtube", "fallback", "success", 0.9, url="https://youtu.be/abc"
    )

    result = runner.invoke(app, ["usage"])

    assert result.exit_code == 0
    assert "Provider attempts — last 30 day(s)" in result.output

    primary = _row_for(result.output, "youtube    primary")
    assert primary.split()[:4] == ["youtube", "primary", "3", "100%"]
    assert "fallback rate" not in primary

    fallback = _row_for(result.output, "youtube    fallback")
    assert fallback.split()[:4] == ["youtube", "fallback", "1", "100%"]
    # 1 fallback attempt out of 4 total youtube attempts.
    assert "(25% fallback rate)" in fallback


def test_usage_omits_the_provider_table_when_nothing_was_attempted(
    usage_db: ObservabilityDB,
) -> None:
    """No provider attempts means no provider section at all."""
    usage_db.record_invocation("search", "success", 0.2)

    result = runner.invoke(app, ["usage"])

    assert result.exit_code == 0
    assert "Provider attempts" not in result.output


def test_usage_exits_when_settings_cannot_be_loaded(usage_db: ObservabilityDB) -> None:
    """get_settings() is a config-presence check; failing it stops the command."""
    with patch(
        "obsidian_ai_tools.commands.vault.get_settings",
        side_effect=RuntimeError("Could not find .env file"),
    ):
        result = runner.invoke(app, ["usage"])

    assert result.exit_code == 1
    assert "❌ Configuration error" in result.stderr
    assert "Could not find .env file" in result.stderr
    assert "Command usage" not in result.output


# ---------------------------------------------------------------------------
# command registration
# ---------------------------------------------------------------------------


def test_register_installs_all_vault_commands_on_a_fresh_app() -> None:
    """register() must wire rebuild-index/process-inbox/usage onto any app."""
    fresh = typer.Typer()
    vault_cmd.register(fresh)
    for command in ("rebuild-index", "process-inbox", "usage"):
        result = runner.invoke(fresh, [command, "--help"])
        assert result.exit_code == 0, f"{command!r} not registered: {result.output}"


# ---------------------------------------------------------------------------
# batch-move summary rendering (process-inbox dry-run / confirm report)
# ---------------------------------------------------------------------------


def _summary_notes() -> list[NoteToMove]:
    return [
        NoteToMove(
            file_path=Path("/vault/inbox/note-a.md"),
            title="Note A",
            tags=["ai", "python"],
            best_folder="AI",
            matched_tags=["ai", "python"],
            score=42.0,
        ),
        NoteToMove(
            file_path=Path("/vault/inbox/note-b.md"),
            title="Note B",
            tags=["ai"],
            best_folder="Development/Python",
            matched_tags=[],
            score=11.5,
        ),
    ]


def test_display_batch_summary_non_dry_run_outputs_exact_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The confirm-phase summary prints one block per note, unmatched notes
    included - called without dry_run so its defaulted False is exercised."""
    vault_cmd._display_batch_summary(_summary_notes())

    assert capsys.readouterr().out == (
        "📋 Found 2 note(s) to move:\n"
        "\n"
        "  📄 note-a.md\n"
        "     Tags: ai, python\n"
        "     → AI (matched: ai, python, score: 42.0)\n"
        "\n"
        "  📄 note-b.md\n"
        "     Tags: ai\n"
        "     → Development/Python (matched: none, score: 11.5)\n"
        "\n"
    )


def test_display_batch_summary_dry_run_outputs_exact_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The dry-run banner is emitted before the same per-note blocks."""
    vault_cmd._display_batch_summary(_summary_notes(), dry_run=True)

    out = capsys.readouterr().out
    assert out.startswith("🔍 DRY RUN - No files will be moved\n\n")
    assert "📋 Found 2 note(s) to move:" not in out
    assert "  📄 note-a.md\n" in out
    assert "     Tags: ai, python\n" in out
    assert "→ AI (matched: ai, python, score: 42.0)" in out
