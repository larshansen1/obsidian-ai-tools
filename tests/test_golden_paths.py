"""Golden-path end-to-end tests for the most important kai workflows.

Each test drives the full path from CLI invocation through to file-system
artifacts, using mocked external APIs (OpenRouter/LLM) where needed.
These are marked `slow` so the fast pre-commit suite can skip them; run
with `pytest -m slow` or as part of the full CI suite.
"""

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from obsidian_ai_tools.cli import app

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _strip(text: str) -> str:
    return ANSI_RE.sub("", text)


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "inbox").mkdir()
    (vault / ".kai").mkdir()
    return vault


def _make_note(path: Path, title: str, tags: list[str], body: str = "Content.") -> None:
    tag_list = ", ".join(tags)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {title}\ntags: [{tag_list}]\n---\n# {title}\n\n{body}\n",
        encoding="utf-8",
    )


def _mock_llm_client(content_json: str) -> MagicMock:
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=content_json))]
    mock_response.usage = MagicMock(prompt_tokens=80, completion_tokens=40, cost=0.001)
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response
    return mock_client


# ---------------------------------------------------------------------------
# Golden path 1 — ingest a local Markdown file → note written to inbox
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_golden_ingest_local_markdown(tmp_path: Path) -> None:
    """CLI: kai ingest <file> creates a structured note in the vault inbox."""
    vault = _make_vault(tmp_path)

    source_file = tmp_path / "research.md"
    source_file.write_text(
        "# AI Agents\n\nAI agents are autonomous systems that perceive and act.",
        encoding="utf-8",
    )

    llm_content = json.dumps(
        {
            "title": "AI Agents Overview",
            "summary": "Autonomous AI systems that perceive and act.",
            "key_points": ["Agents perceive their environment", "Agents take actions"],
            "tags": ["ai", "agents"],
        }
    )

    with (
        patch("obsidian_ai_tools.commands.ingest.get_settings") as mock_settings,
        patch("obsidian_ai_tools.llm.OpenAI") as mock_openai,
    ):
        mock_settings.return_value.obsidian_vault_path = vault
        mock_settings.return_value.obsidian_inbox_folder = "inbox"
        mock_settings.return_value.llm_model = "test-model"
        mock_settings.return_value.openrouter_api_key = "test-key"
        mock_settings.return_value.max_transcript_length = 50000
        mock_openai.return_value = _mock_llm_client(llm_content)

        result = runner.invoke(app, ["ingest", str(source_file), "--vault", str(vault)])

    stdout = _strip(result.stdout)
    assert result.exit_code == 0, f"ingest failed:\n{stdout}\n{result.stderr or ''}"

    inbox_notes = list((vault / "inbox").glob("*.md"))
    assert inbox_notes, "No note written to inbox"

    content = inbox_notes[0].read_text(encoding="utf-8")
    assert "---" in content, "Note has no frontmatter"
    assert "AI Agents Overview" in content
    assert "ai" in content


# ---------------------------------------------------------------------------
# Golden path 2 — rebuild-index scans vault and creates index artifacts
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_golden_rebuild_index(tmp_path: Path) -> None:
    """CLI: kai rebuild-index indexes all vault notes and writes index files."""
    vault = _make_vault(tmp_path)

    _make_note(vault / "inbox" / "note-a.md", "Note A", ["ai", "python"])
    _make_note(vault / "inbox" / "note-b.md", "Note B", ["llm"])
    _make_note(vault / "AI" / "note-c.md", "Note C", ["agents"])

    result = runner.invoke(app, ["rebuild-index", "--vault", str(vault)])
    stdout = _strip(result.stdout)

    assert result.exit_code == 0, f"rebuild-index failed:\n{stdout}"
    assert "Indexed 3 note(s)" in stdout
    assert "Index rebuild complete" in stdout

    assert (vault / ".kai" / "vault_index.json").exists(), "vault_index.json not created"
    assert (vault / ".kai" / "whoosh_index").is_dir(), "whoosh_index not created"


# ---------------------------------------------------------------------------
# Golden path 3 — process-inbox moves notes to folders by tag rules
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_golden_process_inbox(tmp_path: Path) -> None:
    """CLI: kai process-inbox --confirm --yes moves notes per folder_rules.json."""
    vault = _make_vault(tmp_path)

    _make_note(vault / "inbox" / "ai-note.md", "AI Note", ["ai", "llm"])
    _make_note(vault / "inbox" / "python-note.md", "Python Note", ["python"])

    rules = {"ai": "AI", "llm": "AI", "python": "Development/Python"}
    (vault / "folder_rules.json").write_text(json.dumps(rules), encoding="utf-8")

    result = runner.invoke(app, ["process-inbox", "--confirm", "--yes", "--vault", str(vault)])
    stdout = _strip(result.stdout)

    assert result.exit_code == 0, f"process-inbox failed:\n{stdout}"
    assert not (vault / "inbox" / "ai-note.md").exists(), "ai-note.md was not moved"
    assert not (vault / "inbox" / "python-note.md").exists(), "python-note.md was not moved"
    assert (vault / "AI" / "ai-note.md").exists(), "ai-note.md not in AI/"
    assert (vault / "Development" / "Python" / "python-note.md").exists(), (
        "python-note.md not in Development/Python/"
    )


# ---------------------------------------------------------------------------
# Golden path 4 — search returns relevant results after rebuild-index
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_golden_search_after_index(tmp_path: Path) -> None:
    """CLI: kai rebuild-index then kai search --keyword returns matching notes."""
    vault = _make_vault(tmp_path)

    _make_note(
        vault / "inbox" / "transformers.md",
        "Transformers Explained",
        ["ai", "nlp"],
        body="Transformers are a neural network architecture based on attention.",
    )
    _make_note(
        vault / "inbox" / "python-basics.md",
        "Python Basics",
        ["python"],
        body="Python is a high-level programming language.",
    )

    index_result = runner.invoke(app, ["rebuild-index", "--vault", str(vault)])
    assert index_result.exit_code == 0, "rebuild-index failed before search test"

    result = runner.invoke(app, ["search", "--keyword", "transformers", "--vault", str(vault)])
    stdout = _strip(result.stdout)

    assert result.exit_code == 0, f"search failed:\n{stdout}"
    assert "Transformers" in stdout, "Expected search hit not in output"
