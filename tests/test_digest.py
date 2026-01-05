"""Tests for digest generation functionality."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from obsidian_ai_tools.digest import (
    DigestReport,
    NoteSummary,
    count_backlinks,
    extract_summary,
    format_digest_json,
    format_digest_markdown,
    format_digest_terminal,
    generate_digest,
    sanitize_wikilink,
)
from obsidian_ai_tools.indexer import NoteMetadata, VaultIndex

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sample_note() -> NoteMetadata:
    """Create a sample note for testing."""
    return NoteMetadata(
        file_path=Path("/vault/inbox/test-note.md"),
        title="Test Note",
        tags=["ai", "testing"],
        created=datetime.now(),
        author="Test Author",
        source_url="https://example.com",
        source_type="web",
        content=(
            "# Test Note\n\n## Summary\n\nThis is a test summary. "
            "It has multiple sentences.\n\n## Key Points\n\n- Point 1\n- Point 2"
        ),
        modified_time=datetime.now().timestamp(),
    )


@pytest.fixture
def sample_vault_index(tmp_path: Path) -> VaultIndex:
    """Create a sample vault index for testing."""
    now = datetime.now()
    notes = [
        NoteMetadata(
            file_path=tmp_path / "note1.md",
            title="Note One",
            tags=["ai", "llm"],
            created=now - timedelta(days=1),
            source_type="youtube",
            content="See [[Note Two]] for details. Also [[Note Two]] again.",
            modified_time=(now - timedelta(days=1)).timestamp(),
        ),
        NoteMetadata(
            file_path=tmp_path / "note2.md",
            title="Note Two",
            tags=["ai", "programming"],
            created=now - timedelta(days=2),
            source_type="web",
            content="Check out [[Note Three]] for more info.",
            modified_time=(now - timedelta(days=2)).timestamp(),
        ),
        NoteMetadata(
            file_path=tmp_path / "note3.md",
            title="Note Three",
            tags=["programming"],
            created=now - timedelta(days=10),
            source_type=None,  # Manual note
            content="No links here.",
            modified_time=(now - timedelta(days=10)).timestamp(),
        ),
    ]
    return VaultIndex(
        notes=notes,
        index_path=tmp_path / ".kai" / "vault_index.json",
    )


@pytest.fixture
def sample_digest_report() -> DigestReport:
    """Create a sample digest report for formatting tests."""
    now = datetime.now()
    return DigestReport(
        period_start=now - timedelta(days=7),
        period_end=now,
        total_notes=10,
        new_notes=3,
        new_notes_details=[
            NoteSummary(
                title="GPT-4 Vision Tutorial",
                summary="How to use GPT-4V for image analysis.",
                source_type="youtube",
                file_path=Path("/vault/inbox/gpt4.md"),
            ),
            NoteSummary(
                title="Python Best Practices",
                summary="Modern Python coding standards.",
                source_type="web",
                file_path=Path("/vault/inbox/python.md"),
            ),
        ],
        by_source_type={"youtube": 1, "web": 2},
        top_tags=[("ai", 4), ("programming", 3)],
        most_referenced=[("Attention Mechanisms", 3), ("Transformers", 2)],
        inbox_count=5,
    )


# ============================================================================
# Tests for extract_summary
# ============================================================================


class TestExtractSummary:
    """Tests for extract_summary function."""

    def test_extract_summary_from_section(self, sample_note: NoteMetadata) -> None:
        """Test extracting summary from ## Summary section."""
        result = extract_summary(sample_note)
        assert "This is a test summary" in result

    def test_extract_summary_truncates(self) -> None:
        """Test that long summaries are truncated to first sentence."""
        note = NoteMetadata(
            file_path=Path("/vault/test.md"),
            title="Test",
            tags=[],
            content=(
                "## Summary\n\nThis is sentence one. "
                "This is sentence two. This is sentence three."
            ),
            modified_time=datetime.now().timestamp(),
        )
        result = extract_summary(note, max_length=50)
        assert len(result) <= 50
        assert "..." in result or result.endswith(".")

    def test_extract_summary_fallback(self) -> None:
        """Test fallback to first paragraph when no Summary section."""
        note = NoteMetadata(
            file_path=Path("/vault/test.md"),
            title="Test",
            tags=[],
            content="# Title\n\nFirst paragraph content here.\n\nSecond paragraph.",
            modified_time=datetime.now().timestamp(),
        )
        result = extract_summary(note)
        assert "First paragraph content" in result

    def test_extract_summary_empty_content(self) -> None:
        """Test handling of empty content."""
        note = NoteMetadata(
            file_path=Path("/vault/test.md"),
            title="Test",
            tags=[],
            content="",
            modified_time=datetime.now().timestamp(),
        )
        result = extract_summary(note)
        assert result == ""


# ============================================================================
# Tests for sanitize_wikilink
# ============================================================================


class TestSanitizeWikilink:
    """Tests for sanitize_wikilink function."""

    def test_sanitize_wikilink_removes_colons(self) -> None:
        """Test that colons are removed from wikilinks."""
        result = sanitize_wikilink("Windows 11's 2025 Crisis: AI Obsession")
        assert ":" not in result
        assert "Windows 11's 2025 Crisis" in result or "AI Obsession" in result

    def test_sanitize_wikilink_removes_slashes(self) -> None:
        """Test that slashes are removed from wikilinks."""
        result = sanitize_wikilink("Path/To/Note")
        assert "/" not in result
        assert "\\" not in result

    def test_sanitize_wikilink_removes_brackets(self) -> None:
        """Test that brackets are removed from wikilinks."""
        result = sanitize_wikilink("Note [with] brackets")
        assert "[" not in result
        assert "]" not in result

    def test_sanitize_wikilink_preserves_safe_chars(self) -> None:
        """Test that safe characters are preserved."""
        result = sanitize_wikilink("Simple Note Title")
        assert result == "Simple Note Title"

    def test_sanitize_wikilink_collapses_dashes(self) -> None:
        """Test that multiple dashes are collapsed."""
        result = sanitize_wikilink("Note: With: Multiple: Colons")
        assert "---" not in result
        assert "--" not in result


# ============================================================================
# Tests for count_backlinks
# ============================================================================


class TestCountBacklinks:
    """Tests for count_backlinks function."""

    def test_count_backlinks_simple(self, sample_vault_index: VaultIndex) -> None:
        """Test counting a single backlink."""
        result = count_backlinks(sample_vault_index)
        assert "Note Three" in result
        assert result["Note Three"] == 1

    def test_count_backlinks_multiple(self, sample_vault_index: VaultIndex) -> None:
        """Test counting multiple references to same note."""
        result = count_backlinks(sample_vault_index)
        assert "Note Two" in result
        assert result["Note Two"] == 2  # Referenced twice in note1

    def test_count_backlinks_no_links(self, tmp_path: Path) -> None:
        """Test vault with no wikilinks."""
        index = VaultIndex(
            notes=[
                NoteMetadata(
                    file_path=tmp_path / "note.md",
                    title="No Links",
                    tags=[],
                    content="Just plain text here.",
                    modified_time=datetime.now().timestamp(),
                )
            ],
            index_path=tmp_path / ".kai" / "index.json",
        )
        result = count_backlinks(index)
        assert result == {}

    def test_count_backlinks_nested_brackets(self, tmp_path: Path) -> None:
        """Test handling [[Link|Alias]] format."""
        index = VaultIndex(
            notes=[
                NoteMetadata(
                    file_path=tmp_path / "note.md",
                    title="Test",
                    tags=[],
                    content="See [[Target Note|alias text]] for info.",
                    modified_time=datetime.now().timestamp(),
                )
            ],
            index_path=tmp_path / ".kai" / "index.json",
        )
        result = count_backlinks(index)
        assert "Target Note" in result
        assert result["Target Note"] == 1


# ============================================================================
# Tests for generate_digest
# ============================================================================


class TestGenerateDigest:
    """Tests for generate_digest function."""

    def test_generate_digest_empty_vault(self, tmp_path: Path) -> None:
        """Test digest generation with empty vault."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        result = generate_digest(tmp_path, since_days=7, inbox_folder="inbox")

        assert result.total_notes == 0
        assert result.new_notes == 0
        assert result.by_source_type == {}

    def test_generate_digest_with_notes(self, tmp_path: Path) -> None:
        """Test digest generation with actual notes."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        # Create a test note
        note_path = inbox / "test.md"
        created_time = datetime.now().isoformat()
        note_content = f"""---
title: Test Note
tags:
  - ai
  - test
created: {created_time}
source_type: youtube
---

## Summary

This is a test note summary.

## Key Points

- Point 1
"""
        note_path.write_text(note_content)

        result = generate_digest(tmp_path, since_days=7, inbox_folder="inbox")

        assert result.total_notes >= 1
        assert result.inbox_count >= 1

    def test_generate_digest_date_filtering(self, tmp_path: Path) -> None:
        """Test that date filtering works correctly."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        # Create old note (outside date range)
        old_note = inbox / "old.md"
        old_date = (datetime.now() - timedelta(days=30)).isoformat()
        old_note.write_text(f"---\ntitle: Old Note\ncreated: {old_date}\n---\nOld content")

        # Create new note (within date range)
        new_note = inbox / "new.md"
        new_date = datetime.now().isoformat()
        new_note.write_text(f"---\ntitle: New Note\ncreated: {new_date}\n---\nNew content")

        result = generate_digest(tmp_path, since_days=7, inbox_folder="inbox")

        # Should only count the new note
        assert result.new_notes == 1

    def test_generate_digest_source_type_breakdown(self, tmp_path: Path) -> None:
        """Test source type counting."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        # Create notes with different source types
        for i, source_type in enumerate(["youtube", "web", "youtube"]):
            note = inbox / f"note{i}.md"
            note.write_text(
                f"---\ntitle: Note {i}\ncreated: {datetime.now().isoformat()}\n"
                f"source_type: {source_type}\n---\nContent"
            )

        result = generate_digest(tmp_path, since_days=7, inbox_folder="inbox")

        assert result.by_source_type.get("youtube", 0) == 2
        assert result.by_source_type.get("web", 0) == 1

    def test_generate_digest_manual_notes(self, tmp_path: Path) -> None:
        """Test that notes without source_type are counted as manual."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        note = inbox / "manual.md"
        note.write_text(
            f"---\ntitle: Manual Note\ncreated: {datetime.now().isoformat()}\n---\nContent"
        )

        result = generate_digest(tmp_path, since_days=7, inbox_folder="inbox")

        assert result.by_source_type.get("manual", 0) == 1

    def test_generate_digest_top_tags(self, tmp_path: Path) -> None:
        """Test tag frequency counting."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        # Create notes with overlapping tags
        for i in range(3):
            note = inbox / f"note{i}.md"
            tags = "  - ai\n  - programming" if i < 2 else "  - ai"
            note.write_text(
                f"---\ntitle: Note {i}\ncreated: {datetime.now().isoformat()}\n"
                f"tags:\n{tags}\n---\nContent"
            )

        result = generate_digest(tmp_path, since_days=7, inbox_folder="inbox")

        # Check tags are sorted by frequency
        tag_names = [t[0] for t in result.top_tags]
        assert "ai" in tag_names
        if "programming" in tag_names:
            ai_idx = tag_names.index("ai")
            prog_idx = tag_names.index("programming")
            assert ai_idx < prog_idx  # ai should come first (3 vs 2)

    def test_generate_digest_most_referenced(self, tmp_path: Path) -> None:
        """Test backlink counting in digest."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        # Create notes with wikilinks
        note1 = inbox / "note1.md"
        note1.write_text(
            f"---\ntitle: Note 1\ncreated: {datetime.now().isoformat()}\n---\n"
            "See [[Target]] and [[Target]] and [[Other]]"
        )

        note2 = inbox / "target.md"
        note2.write_text(
            f"---\ntitle: Target\ncreated: {datetime.now().isoformat()}\n---\nContent"
        )

        result = generate_digest(tmp_path, since_days=7, inbox_folder="inbox")

        # Target should be most referenced
        if result.most_referenced:
            assert result.most_referenced[0][0] == "Target"
            assert result.most_referenced[0][1] == 2

    def test_generate_digest_new_notes_details(self, tmp_path: Path) -> None:
        """Test that new_notes_details is populated correctly."""
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        note = inbox / "test.md"
        note.write_text(
            f"---\ntitle: Test Note\ncreated: {datetime.now().isoformat()}\n"
            "source_type: youtube\n---\n\n## Summary\n\nThis is the summary."
        )

        result = generate_digest(tmp_path, since_days=7, inbox_folder="inbox")

        assert len(result.new_notes_details) >= 1
        detail = result.new_notes_details[0]
        assert detail.title == "Test Note"
        assert detail.source_type == "youtube"
        assert "summary" in detail.summary.lower() or detail.summary != ""


# ============================================================================
# Tests for formatters
# ============================================================================


class TestFormatDigestTerminal:
    """Tests for format_digest_terminal function."""

    def test_format_digest_terminal(self, sample_digest_report: DigestReport) -> None:
        """Test terminal formatting produces emoji output."""
        result = format_digest_terminal(sample_digest_report)

        assert "📊" in result  # Header emoji
        assert "📝" in result  # Notes section
        assert "📦" in result  # Source type section
        assert "🏷️" in result  # Tags section
        assert "🔗" in result  # Referenced section
        assert "📥" in result  # Inbox section

    def test_format_digest_terminal_includes_notes(
        self, sample_digest_report: DigestReport
    ) -> None:
        """Test terminal output includes note details."""
        result = format_digest_terminal(sample_digest_report)

        assert "GPT-4 Vision Tutorial" in result
        assert "Python Best Practices" in result


class TestFormatDigestMarkdown:
    """Tests for format_digest_markdown function."""

    def test_format_digest_markdown(self, sample_digest_report: DigestReport) -> None:
        """Test markdown formatting produces valid markdown."""
        result = format_digest_markdown(sample_digest_report)

        assert result.startswith("---")  # Frontmatter
        assert "# Knowledge Digest" in result
        assert "## 📝 Notes Summary" in result
        assert "## 📦 By Source Type" in result

    def test_format_digest_markdown_note_table(
        self, sample_digest_report: DigestReport
    ) -> None:
        """Test markdown includes note listing tables."""
        result = format_digest_markdown(sample_digest_report)

        assert "| Note | Summary |" in result
        # Wikilinks use filename stem (without .md) not title
        assert "[[gpt4]]" in result
        assert "[[python]]" in result

    def test_format_digest_markdown_frontmatter(
        self, sample_digest_report: DigestReport
    ) -> None:
        """Test markdown has proper frontmatter."""
        result = format_digest_markdown(sample_digest_report)

        assert "title:" in result
        assert "tags:" in result
        assert "- digest" in result
        assert "type: digest" in result


class TestFormatDigestJson:
    """Tests for format_digest_json function."""

    def test_format_digest_json(self, sample_digest_report: DigestReport) -> None:
        """Test JSON formatting produces valid JSON."""
        result = format_digest_json(sample_digest_report)

        # Should parse without error
        data = json.loads(result)

        assert data["total_notes"] == 10
        assert data["new_notes"] == 3
        assert data["inbox_count"] == 5

    def test_format_digest_json_includes_details(
        self, sample_digest_report: DigestReport
    ) -> None:
        """Test JSON includes note details."""
        result = format_digest_json(sample_digest_report)
        data = json.loads(result)

        assert "new_notes_details" in data
        assert len(data["new_notes_details"]) == 2
        assert data["new_notes_details"][0]["title"] == "GPT-4 Vision Tutorial"


# ============================================================================
# CLI Integration Tests
# ============================================================================


class TestDigestCommand:
    """Integration tests for kai digest CLI command."""

    def test_digest_command_runs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that kai digest command runs without error."""
        from typer.testing import CliRunner

        from obsidian_ai_tools.cli import app

        # Create minimal vault structure
        inbox = tmp_path / "inbox"
        inbox.mkdir()

        # Mock settings
        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("OBSIDIAN_INBOX_FOLDER", "inbox")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("LLM_MODEL", "test-model")

        runner = CliRunner()
        result = runner.invoke(app, ["digest", "--vault", str(tmp_path)])

        assert result.exit_code == 0
        assert "📊" in result.output or "Generating digest" in result.output

    def test_digest_command_days_option(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test --days option changes the period."""
        from typer.testing import CliRunner

        from obsidian_ai_tools.cli import app

        inbox = tmp_path / "inbox"
        inbox.mkdir()

        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("OBSIDIAN_INBOX_FOLDER", "inbox")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("LLM_MODEL", "test-model")

        runner = CliRunner()
        result = runner.invoke(app, ["digest", "--days", "1", "--vault", str(tmp_path)])

        assert result.exit_code == 0
        assert "1 day" in result.output

    def test_digest_command_format_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test --format json produces valid JSON."""
        from typer.testing import CliRunner

        from obsidian_ai_tools.cli import app

        inbox = tmp_path / "inbox"
        inbox.mkdir()

        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("OBSIDIAN_INBOX_FOLDER", "inbox")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("LLM_MODEL", "test-model")

        runner = CliRunner()
        result = runner.invoke(
            app, ["digest", "--format", "json", "--vault", str(tmp_path)]
        )

        assert result.exit_code == 0
        # Output should contain JSON (after the progress message)
        assert "{" in result.output

    def test_digest_command_output_creates_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test --output creates markdown file in inbox."""
        from typer.testing import CliRunner

        from obsidian_ai_tools.cli import app

        inbox = tmp_path / "inbox"
        inbox.mkdir()

        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("OBSIDIAN_INBOX_FOLDER", "inbox")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("LLM_MODEL", "test-model")

        runner = CliRunner()
        result = runner.invoke(
            app, ["digest", "--output", "test-digest", "--vault", str(tmp_path)]
        )

        assert result.exit_code == 0
        assert "saved to" in result.output.lower()

        # Check file was created with the user-provided name
        output_file = inbox / "test-digest.md"
        assert output_file.exists()

    def test_digest_command_empty_vault(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test digest handles empty vault gracefully."""
        from typer.testing import CliRunner

        from obsidian_ai_tools.cli import app

        inbox = tmp_path / "inbox"
        inbox.mkdir()

        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("OBSIDIAN_INBOX_FOLDER", "inbox")
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("LLM_MODEL", "test-model")

        runner = CliRunner()
        result = runner.invoke(app, ["digest", "--vault", str(tmp_path)])

        assert result.exit_code == 0
        # Should show 0 notes gracefully
        assert "0" in result.output or "Total in vault" in result.output
