"""Integration tests for the CLI."""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import typer
from typer.testing import CliRunner

from obsidian_ai_tools.cli import app

runner = CliRunner()


def test_version_command() -> None:
    """Test the version command."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "obsidian-ai-tools" in result.stdout


def test_help_command() -> None:
    """Test the help command."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Knowledge AI Tools" in result.stdout


def test_search_help() -> None:
    """Test search command help."""
    result = runner.invoke(app, ["search", "--help"])
    assert result.exit_code == 0
    assert "Search your Obsidian vault" in result.stdout


def test_rebuild_index_command(tmp_path: Path) -> None:
    """Test rebuild-index command creates both indexes and scans entire vault."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()

    # Create test notes in inbox folder
    inbox_path = vault_path / "inbox"
    inbox_path.mkdir()
    note1 = inbox_path / "note1.md"
    note1.write_text(
        """---
title: First Note
tags: [ai, python]
---
# First Note Content
""",
        encoding="utf-8",
    )

    # Create notes in other folders to verify recursive scanning
    projects_path = vault_path / "projects"
    projects_path.mkdir()
    note2 = projects_path / "note2.md"
    note2.write_text(
        """---
title: Second Note
tags: [llm]
---
# Second Note Content
""",
        encoding="utf-8",
    )

    archive_path = vault_path / "archive"
    archive_path.mkdir()
    note3 = archive_path / "note3.md"
    note3.write_text(
        """---
title: Third Note
tags: [testing]
---
# Third Note Content
""",
        encoding="utf-8",
    )

    # Run rebuild-index command
    result = runner.invoke(app, ["rebuild-index", "--vault", str(vault_path)])

    # Verify command succeeded
    assert result.exit_code == 0
    assert "Rebuilding indexes" in result.stdout
    # Should find all 3 notes across all folders
    assert "Indexed 3 note(s)" in result.stdout
    assert "Index rebuild complete" in result.stdout

    # Verify indexes were created
    vault_index_path = vault_path / ".kai" / "vault_index.json"
    whoosh_index_path = vault_path / ".kai" / "whoosh_index"

    assert vault_index_path.exists(), "Vault index should be created"
    assert whoosh_index_path.exists(), "Whoosh index directory should be created"
    assert whoosh_index_path.is_dir(), "Whoosh index should be a directory"


def test_process_inbox_requires_confirm(tmp_path: Path) -> None:
    """Test process-inbox requires --confirm flag."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()

    inbox_path = vault_path / "inbox"
    inbox_path.mkdir()

    note_path = inbox_path / "test.md"
    note_path.write_text(
        """---
title: Test Note
tags: [ai, python]
---
# Test Content
""",
        encoding="utf-8",
    )

    rules_path = vault_path / "folder_rules.json"
    rules_path.write_text('{"ai": "AI", "python": "Python"}', encoding="utf-8")

    result = runner.invoke(app, ["process-inbox", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "Add --confirm to execute moves" in result.stdout


def test_process_inbox_confirm_without_prompt(tmp_path: Path) -> None:
    """Test process-inbox with --confirm --yes skips confirmation."""
    vault_path = tmp_path / "mock_vault"
    vault_path.mkdir()

    inbox_path = vault_path / "inbox"
    inbox_path.mkdir()

    note_path = inbox_path / "test.md"
    note_path.write_text(
        """---
title: Test Note
tags: [ai]
---
# Test Content
""",
        encoding="utf-8",
    )

    rules_path = vault_path / "folder_rules.json"
    rules_path.write_text('{"ai": "AI"}', encoding="utf-8")

    result = runner.invoke(app, ["process-inbox", "--confirm", "--yes", "--vault", str(vault_path)])

    assert result.exit_code == 0
    assert "Moved test.md" in result.stdout or "Moved 1/1" in result.stdout


def _make_dummy_settings(vault_path: Path) -> MagicMock:
    """Create dummy settings object for CLI tests."""
    settings = MagicMock()
    settings.obsidian_vault_path = vault_path
    settings.obsidian_inbox_folder = "inbox"
    settings.obsidian_flashcards_folder = "Flashcards"
    settings.llm_model = "test-model"
    settings.openrouter_api_key = "test-key"
    return settings


def test_search_requires_criteria(tmp_path: Path) -> None:
    """Test search command requires at least one criterion."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()

    with patch(
        "obsidian_ai_tools.commands.search.get_settings",
        return_value=_make_dummy_settings(vault_path),
    ):
        result = runner.invoke(app, ["search", "--vault", str(vault_path)])

    assert result.exit_code == 1
    assert "No search criteria provided" in result.output


def test_search_invalid_after_date(tmp_path: Path) -> None:
    """Test search command validates --after date format."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()

    with patch(
        "obsidian_ai_tools.commands.search.get_settings",
        return_value=_make_dummy_settings(vault_path),
    ):
        result = runner.invoke(
            app,
            [
                "search",
                "--after",
                "not-a-date",
                "--vault",
                str(vault_path),
            ],
        )

    assert result.exit_code == 1
    assert "Invalid date format for --after" in result.output


def test_search_no_results(tmp_path: Path) -> None:
    """Test search command handles no results cleanly."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()

    dummy_settings = _make_dummy_settings(vault_path)

    with (
        patch(
            "obsidian_ai_tools.commands.search.get_settings",
            return_value=dummy_settings,
        ),
        patch("obsidian_ai_tools.indexer.build_index", return_value=MagicMock()),
        patch(
            "obsidian_ai_tools.search.build_whoosh_index",
            return_value=None,
        ),
        patch("obsidian_ai_tools.search.search_notes", return_value=[]),
    ):
        result = runner.invoke(
            app,
            [
                "search",
                "--keyword",
                "ai",
                "--vault",
                str(vault_path),
            ],
        )

    assert result.exit_code == 0
    assert "No results found" in result.output


def test_search_with_results(tmp_path: Path) -> None:
    """Test search command prints formatted results with previews."""
    vault_path = tmp_path / "vault"
    vault_path.mkdir()

    class DummyNote:
        def __init__(self, file_path: Path) -> None:
            self.file_path = file_path
            self.title = "Test Note"
            self.tags = ["ai", "cli"]
            self.created = None
            self.author = "Tester"

    class DummyResult:
        def __init__(self, note: DummyNote, highlights: str) -> None:
            self.note = note
            self.highlights = highlights
            self.explanation = None
            self.outgoing_links: list[str] = []

    dummy_settings = _make_dummy_settings(vault_path)
    note = DummyNote(vault_path / "inbox" / "test.md")
    results = [DummyResult(note, "<b>Preview</b> content")]  # HTML to be stripped

    with (
        patch(
            "obsidian_ai_tools.commands.search.get_settings",
            return_value=dummy_settings,
        ),
        patch("obsidian_ai_tools.indexer.build_index", return_value=MagicMock()),
        patch(
            "obsidian_ai_tools.search.build_whoosh_index",
            return_value=None,
        ),
        patch("obsidian_ai_tools.search.search_notes", return_value=results),
    ):
        result = runner.invoke(
            app,
            [
                "search",
                "--keyword",
                "ai",
                "--vault",
                str(vault_path),
            ],
        )

    assert result.exit_code == 0
    assert "Found 1 result(s)" in result.output
    assert "Test Note" in result.output
    # Preview line should have HTML stripped
    assert "Preview: Preview content" in result.output


def test_serve_background_starts_detached_process(tmp_path: Path) -> None:
    """Test serve --background starts a detached child and records its PID."""
    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path),
        patch("obsidian_ai_tools.commands.serve.subprocess.Popen") as mock_popen,
    ):
        mock_popen.return_value.pid = 4321

        result = runner.invoke(app, ["serve", "--background", "--port", "9000"])

    assert result.exit_code == 0
    assert "started in the background on http://127.0.0.1:9000" in result.output
    assert (tmp_path / ".kai" / "server.pid").read_text(encoding="utf-8") == "4321\n"
    mock_popen.assert_called_once()
    command = mock_popen.call_args.args[0]
    assert command[-4:] == ["--host", "127.0.0.1", "--port", "9000"]
    assert mock_popen.call_args.kwargs["start_new_session"] is True


def test_serve_status_reports_running_background_process(tmp_path: Path) -> None:
    """Test serve --status reads the PID file and checks that process."""
    state_dir = tmp_path / ".kai"
    state_dir.mkdir()
    (state_dir / "server.pid").write_text("4321\n", encoding="utf-8")

    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path),
        patch("obsidian_ai_tools.commands.serve.os.kill") as mock_kill,
    ):
        result = runner.invoke(app, ["serve", "--status"])

    assert result.exit_code == 0
    assert "running in the background (PID 4321)" in result.output
    mock_kill.assert_called_once_with(4321, 0)


def test_serve_status_log_shows_recent_background_output(tmp_path: Path) -> None:
    """Test serve --status --log prints only the most recent log lines."""
    state_dir = tmp_path / ".kai"
    state_dir.mkdir()
    (state_dir / "server.log").write_text(
        "\n".join(f"log line {number}" for number in range(25)),
        encoding="utf-8",
    )

    with patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path):
        result = runner.invoke(app, ["serve", "--status", "--log"])

    assert result.exit_code == 0
    assert "kai server is not running in the background." in result.output
    assert "Recent log output" in result.output
    assert "log line 4" not in result.output
    assert "log line 5" in result.output
    assert "log line 24" in result.output


def test_serve_log_requires_status() -> None:
    """Test serve --log is only accepted as a status modifier."""
    result = runner.invoke(app, ["serve", "--log"])

    assert result.exit_code == 1
    assert "--log must be used with --status" in result.output


def test_serve_stop_terminates_background_process(tmp_path: Path) -> None:
    """Test serve --stop terminates the recorded background process."""
    state_dir = tmp_path / ".kai"
    state_dir.mkdir()
    pid_path = state_dir / "server.pid"
    pid_path.write_text("4321\n", encoding="utf-8")

    with (
        patch("obsidian_ai_tools.commands.serve.Path.home", return_value=tmp_path),
        patch("obsidian_ai_tools.commands.serve.os.kill") as mock_kill,
    ):
        result = runner.invoke(app, ["serve", "--stop"])

    assert result.exit_code == 0
    assert "kai server stopped (PID 4321)" in result.output
    assert not pid_path.exists()
    assert mock_kill.call_args_list == [call(4321, 0), call(4321, 15)]


# ---------------------------------------------------------------------------
# command registration (fresh apps, not the shared cli app)
# ---------------------------------------------------------------------------


def test_ingest_register_installs_command_on_a_fresh_app() -> None:
    """Each commands module's register() must wire itself onto any Typer app."""
    from obsidian_ai_tools.commands import ingest

    fresh = typer.Typer()
    ingest.register(fresh)
    result = runner.invoke(fresh, ["ingest", "--help"])
    assert result.exit_code == 0, f"ingest not registered: {result.output}"


def test_preview_register_installs_command_on_a_fresh_app() -> None:
    from obsidian_ai_tools.commands import preview

    fresh = typer.Typer()
    preview.register(fresh)
    assert preview._app is fresh
    result = runner.invoke(fresh, ["preview", "--help"])
    assert result.exit_code == 0, f"preview not registered: {result.output}"


def test_search_register_installs_command_on_a_fresh_app() -> None:
    from obsidian_ai_tools.commands import search

    fresh = typer.Typer()
    search.register(fresh)
    result = runner.invoke(fresh, ["search", "--help"])
    assert result.exit_code == 0, f"search not registered: {result.output}"
