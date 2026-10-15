"""Tests for `kai process-inbox` and its helpers in commands/vault.py.

The helpers are driven directly with capsys for exact output; the command
itself runs through CliRunner against a real vault in tmp_path, with settings
patched so the inbox folder name is fixed.
"""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
import typer
from typer.testing import CliRunner

from obsidian_ai_tools.cli import app
from obsidian_ai_tools.commands import vault as vault_cmd
from obsidian_ai_tools.folder_organizer import MoveResult, NoteToMove

runner = CliRunner()


def _settings(vault_path: Path) -> MagicMock:
    settings = MagicMock()
    settings.obsidian_vault_path = vault_path
    settings.obsidian_inbox_folder = "inbox"
    return settings


def _vault_with_note(tmp_path: Path) -> Path:
    vault_path = tmp_path / "vault"
    (vault_path / "inbox").mkdir(parents=True)
    (vault_path / "inbox" / "note.md").write_text(
        "---\ntitle: Note\ntags: [ai]\n---\n# Body\n", encoding="utf-8"
    )
    (vault_path / "folder_rules.json").write_text('{"ai": "AI"}', encoding="utf-8")
    return vault_path


# ------------------------------- _warn_unparsed -------------------------------


def test_warn_unparsed_names_all_five_without_more(capsys) -> None:
    vault_cmd._warn_unparsed(["a", "b", "c", "d", "e"])
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == "⚠️  Warning: Could not parse 5 file(s): a, b, c, d, e\n"


def test_warn_unparsed_counts_files_past_five(capsys) -> None:
    vault_cmd._warn_unparsed(["a", "b", "c", "d", "e", "f"])
    out = capsys.readouterr()
    assert out.err == "⚠️  Warning: Could not parse 6 file(s): a, b, c, d, e and 1 more\n"


# --------------------------------- _confirmed ---------------------------------


def test_confirmed_without_confirm_flag_refuses(capsys) -> None:
    with patch.object(vault_cmd.typer, "confirm") as prompt:
        assert vault_cmd._confirmed(3, confirm=False, yes=True) is False
    prompt.assert_not_called()
    assert capsys.readouterr().out == "\n⚠️  Add --confirm to execute moves\n"


def test_confirmed_with_yes_skips_prompt(capsys) -> None:
    with patch.object(vault_cmd.typer, "confirm") as prompt:
        assert vault_cmd._confirmed(3, confirm=True, yes=True) is True
    prompt.assert_not_called()
    assert capsys.readouterr().out == ""


def test_confirmed_prompt_declined_cancels(capsys) -> None:
    with patch.object(vault_cmd.typer, "confirm", return_value=False) as prompt:
        assert vault_cmd._confirmed(3, confirm=True, yes=False) is False
    prompt.assert_called_once_with("\n❓ Move 3 note(s)?")
    assert capsys.readouterr().out == "❌ Cancelled\n"


def test_confirmed_prompt_accepted_proceeds(capsys) -> None:
    with patch.object(vault_cmd.typer, "confirm", return_value=True) as prompt:
        assert vault_cmd._confirmed(2, confirm=True, yes=False) is True
    prompt.assert_called_once_with("\n❓ Move 2 note(s)?")
    assert capsys.readouterr().out == "\n💾 Moving notes...\n"


# --------------------------------- _move_notes --------------------------------


def test_move_notes_tracks_successes_and_reports_failures(tmp_path: Path, capsys) -> None:
    good = NoteToMove(file_path=tmp_path / "good.md", title="Good", best_folder="AI")
    bad = NoteToMove(file_path=tmp_path / "bad.md", title="Bad", best_folder="AI")
    moved = MoveResult(file="good.md", from_folder="inbox", to_folder="AI", success=True)
    failed = MoveResult(
        file="bad.md", from_folder="inbox", to_folder="AI", success=False, error="disk full"
    )
    with (
        patch("obsidian_ai_tools.folder_organizer.move_note", side_effect=[moved, failed]) as mv,
        patch("obsidian_ai_tools.folder_organizer.track_move") as track,
    ):
        vault_cmd._move_notes([good, bad], tmp_path)

    assert mv.call_args_list == [call(good, tmp_path), call(bad, tmp_path)]
    track.assert_called_once_with(moved, tmp_path)
    out = capsys.readouterr()
    assert out.out == "   ✓ Moved good.md → AI\n\n✅ Successfully moved 1/2 note(s)\n"
    assert out.err == "   ✗ Failed to move bad.md: disk full\n"


# ----------------------------- _load_rules_or_exit ----------------------------


def test_load_rules_returns_rules_and_reports_count(tmp_path: Path, capsys) -> None:
    (tmp_path / "folder_rules.json").write_text('{"ai": "AI", "py": "Dev"}', encoding="utf-8")
    assert vault_cmd._load_rules_or_exit(tmp_path) == {"ai": "AI", "py": "Dev"}
    assert capsys.readouterr().out == "📂 Loading folder rules...\n   ✓ Loaded 2 rule(s)\n"


def test_load_rules_missing_file_exits_with_example(tmp_path: Path, capsys) -> None:
    with pytest.raises(typer.Exit) as exc_info:
        vault_cmd._load_rules_or_exit(tmp_path)
    assert exc_info.value.exit_code == 1
    err = capsys.readouterr().err.splitlines()
    assert err[0].startswith("❌ ")
    assert err[1:] == [
        "💡 Create folder_rules.json in your vault root. Example:",
        "   {",
        '     "ai": "AI & Machine Learning",',
        '     "python": "Development/Python"',
        "   }",
    ]


# ------------------------------ process-inbox CLI -----------------------------


def test_process_inbox_config_error_exits(tmp_path: Path) -> None:
    with patch.object(vault_cmd, "get_settings", side_effect=ValueError("no vault")):
        result = runner.invoke(app, ["process-inbox", "--vault", str(tmp_path)])
    assert result.exit_code == 1
    assert result.stderr == (
        "❌ Configuration error: no vault\n"
        "💡 Make sure you have a .env file with required settings.\n"
    )


def test_process_inbox_empty_inbox_moves_nothing(tmp_path: Path) -> None:
    vault_path = _vault_with_note(tmp_path)
    (vault_path / "inbox" / "note.md").unlink()
    with patch.object(vault_cmd, "get_settings", return_value=_settings(vault_path)):
        result = runner.invoke(app, ["process-inbox", "--confirm", "--yes"])
    assert result.exit_code == 0
    assert result.stdout.splitlines()[-1] == (
        "✅ No notes to move (inbox is empty or no notes match rules)"
    )


def test_process_inbox_dry_run_leaves_note_in_place(tmp_path: Path) -> None:
    vault_path = _vault_with_note(tmp_path)
    with patch.object(vault_cmd, "get_settings", return_value=_settings(vault_path)):
        result = runner.invoke(
            app, ["process-inbox", "--dry-run", "--confirm", "--yes", "--vault", str(vault_path)]
        )
    assert result.exit_code == 0
    assert "🔍 DRY RUN - No files will be moved" in result.stdout.splitlines()
    assert (vault_path / "inbox" / "note.md").exists()
    assert not (vault_path / "AI" / "note.md").exists()


def test_process_inbox_confirm_yes_moves_note(tmp_path: Path) -> None:
    vault_path = _vault_with_note(tmp_path)
    with patch.object(vault_cmd, "get_settings", return_value=_settings(vault_path)):
        result = runner.invoke(app, ["process-inbox", "--confirm", "--yes"])
    assert result.exit_code == 0
    assert result.stdout.splitlines()[-1] == "✅ Successfully moved 1/1 note(s)"
    assert not (vault_path / "inbox" / "note.md").exists()
    assert (vault_path / "AI" / "note.md").exists()
