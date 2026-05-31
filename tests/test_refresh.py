"""Tests for the refresh module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from obsidian_ai_tools.refresh import (
    RefreshCandidate,
    RefreshResult,
    RefreshSummary,
    create_backup,
    estimate_refresh_cost,
    find_refresh_candidates,
    parse_frontmatter,
    refresh_batch,
    refresh_note,
)


class TestParseFrontmatter:
    """Tests for frontmatter parsing."""

    def test_parse_frontmatter_with_prompt_version(self, tmp_path: Path) -> None:
        """Parse note with prompt_version in frontmatter."""
        note = tmp_path / "test.md"
        note.write_text(
            """---
title: Test Note
tags: [ai, python]
prompt_version: youtube_v1
source_url: https://youtube.com/watch?v=abc123
source_type: youtube
---
# Content
""",
            encoding="utf-8",
        )

        metadata = parse_frontmatter(note)

        assert metadata["title"] == "Test Note"
        assert metadata["prompt_version"] == "youtube_v1"
        assert metadata["source_url"] == "https://youtube.com/watch?v=abc123"
        assert metadata["source_type"] == "youtube"
        assert metadata["tags"] == ["ai", "python"]

    def test_parse_frontmatter_missing_version(self, tmp_path: Path) -> None:
        """Parse note without prompt_version."""
        note = tmp_path / "test.md"
        note.write_text(
            """---
title: Test Note
---
# Content
""",
            encoding="utf-8",
        )

        metadata = parse_frontmatter(note)

        assert metadata["title"] == "Test Note"
        assert "prompt_version" not in metadata

    def test_parse_frontmatter_no_frontmatter(self, tmp_path: Path) -> None:
        """Parse note without frontmatter block."""
        note = tmp_path / "test.md"
        note.write_text("# Just a heading\n\nSome content.", encoding="utf-8")

        metadata = parse_frontmatter(note)

        assert metadata == {}

    def test_parse_frontmatter_with_quoted_values(self, tmp_path: Path) -> None:
        """Parse note with quoted values in frontmatter."""
        note = tmp_path / "test.md"
        note.write_text(
            """---
title: "AI: The Future"
author: 'John Doe'
---
# Content
""",
            encoding="utf-8",
        )

        metadata = parse_frontmatter(note)

        assert metadata["title"] == "AI: The Future"
        assert metadata["author"] == "John Doe"


class TestFindRefreshCandidates:
    """Tests for candidate discovery."""

    def test_find_candidates_basic(self, tmp_path: Path) -> None:
        """Find candidates with different prompt versions."""
        vault = tmp_path / "vault"
        vault.mkdir()

        # Note with v1 (should be found)
        inbox = vault / "inbox"
        inbox.mkdir()
        (inbox / "note1.md").write_text(
            """---
title: Note 1
prompt_version: youtube_v1
source_url: https://youtube.com/watch?v=1
source_type: youtube
---
# Content
""",
            encoding="utf-8",
        )

        # Note with v2 (should NOT be found - already at target)
        (vault / "note2.md").write_text(
            """---
title: Note 2
prompt_version: youtube_v2
source_url: https://youtube.com/watch?v=2
source_type: youtube
---
# Content
""",
            encoding="utf-8",
        )

        candidates = find_refresh_candidates(vault, target_version="youtube_v2")

        assert len(candidates) == 1
        assert candidates[0].title == "Note 1"
        assert candidates[0].current_prompt_version == "youtube_v1"
        assert candidates[0].target_prompt_version == "youtube_v2"

    def test_find_candidates_by_tag(self, tmp_path: Path) -> None:
        """Filter candidates by tag."""
        vault = tmp_path / "vault"
        vault.mkdir()

        # Note with ai tag
        (vault / "note1.md").write_text(
            """---
title: AI Note
tags: [ai]
prompt_version: youtube_v1
source_url: https://youtube.com/watch?v=1
source_type: youtube
---
""",
            encoding="utf-8",
        )

        # Note with python tag
        (vault / "note2.md").write_text(
            """---
title: Python Note
tags: [python]
prompt_version: youtube_v1
source_url: https://youtube.com/watch?v=2
source_type: youtube
---
""",
            encoding="utf-8",
        )

        candidates = find_refresh_candidates(vault, target_version="youtube_v2", tag="ai")

        assert len(candidates) == 1
        assert candidates[0].title == "AI Note"

    def test_find_candidates_by_current_version(self, tmp_path: Path) -> None:
        """Filter candidates by current prompt version."""
        vault = tmp_path / "vault"
        vault.mkdir()

        # Note with youtube_v1
        (vault / "note1.md").write_text(
            """---
title: YouTube V1 Note
prompt_version: youtube_v1
source_url: https://youtube.com/watch?v=1
source_type: youtube
---
""",
            encoding="utf-8",
        )

        # Note with article_v1
        (vault / "note2.md").write_text(
            """---
title: Article Note
prompt_version: article_v1
source_url: https://example.com/article
source_type: web
---
""",
            encoding="utf-8",
        )

        candidates = find_refresh_candidates(
            vault, target_version="youtube_v2", current_version="youtube_v1"
        )

        assert len(candidates) == 1
        assert candidates[0].title == "YouTube V1 Note"

    def test_find_candidates_skips_backups(self, tmp_path: Path) -> None:
        """Skip backup files."""
        vault = tmp_path / "vault"
        vault.mkdir()

        # Original note
        (vault / "note.md").write_text(
            """---
title: Original
prompt_version: youtube_v1
source_url: https://youtube.com/watch?v=1
source_type: youtube
---
""",
            encoding="utf-8",
        )

        # Backup file (should be skipped)
        (vault / "note.backup.md").write_text(
            """---
title: Backup
prompt_version: youtube_v1
source_url: https://youtube.com/watch?v=1
source_type: youtube
---
""",
            encoding="utf-8",
        )

        candidates = find_refresh_candidates(vault, target_version="youtube_v2")

        assert len(candidates) == 1
        assert candidates[0].title == "Original"

    def test_find_candidates_requires_source_url(self, tmp_path: Path) -> None:
        """Skip notes without source_url."""
        vault = tmp_path / "vault"
        vault.mkdir()

        # Note without source_url (manual note)
        (vault / "manual.md").write_text(
            """---
title: Manual Note
prompt_version: youtube_v1
---
""",
            encoding="utf-8",
        )

        # Note with source_url and matching source_type
        (vault / "ingested.md").write_text(
            """---
title: Ingested Note
prompt_version: youtube_v1
source_url: https://youtube.com/watch?v=abc
source_type: youtube
---
""",
            encoding="utf-8",
        )

        candidates = find_refresh_candidates(vault, target_version="youtube_v2")

        assert len(candidates) == 1
        assert candidates[0].title == "Ingested Note"

    def test_find_candidates_filters_by_source_type(self, tmp_path: Path) -> None:
        """Only match notes where target prompt matches source type."""
        vault = tmp_path / "vault"
        vault.mkdir()

        # YouTube note (should match youtube_v2)
        (vault / "youtube_note.md").write_text(
            """---
title: YouTube Note
prompt_version: youtube_v1
source_url: https://youtube.com/watch?v=1
source_type: youtube
---
""",
            encoding="utf-8",
        )

        # PDF note (should NOT match youtube_v2)
        (vault / "pdf_note.md").write_text(
            """---
title: PDF Note
prompt_version: pdf_v1
source_url: https://example.com/doc.pdf
source_type: pdf
---
""",
            encoding="utf-8",
        )

        # Web note (should NOT match youtube_v2)
        (vault / "web_note.md").write_text(
            """---
title: Web Note
prompt_version: article_v1
source_url: https://example.com/article
source_type: web
---
""",
            encoding="utf-8",
        )

        # Search for youtube_v2 - should ONLY find the YouTube note
        candidates = find_refresh_candidates(vault, target_version="youtube_v2")

        assert len(candidates) == 1
        assert candidates[0].title == "YouTube Note"
        assert candidates[0].source_type == "youtube"

        # Search for article_v2 - should ONLY find the web note
        web_candidates = find_refresh_candidates(vault, target_version="article_v2")

        assert len(web_candidates) == 1
        assert web_candidates[0].title == "Web Note"
        assert web_candidates[0].source_type == "web"


class TestEstimateRefreshCost:
    """Tests for cost estimation."""

    def test_estimate_cost_youtube(self) -> None:
        """Estimate cost for YouTube notes."""
        candidates = [
            RefreshCandidate(
                file_path=Path("/test/note.md"),
                title="Test",
                current_prompt_version="youtube_v1",
                target_prompt_version="youtube_v2",
                source_url="https://youtube.com/watch?v=abc",
                source_type="youtube",
            )
        ]

        cost = estimate_refresh_cost(candidates)

        assert cost == 0.02  # YouTube cost estimate

    def test_estimate_cost_multiple_types(self) -> None:
        """Estimate cost for mixed source types."""
        candidates = [
            RefreshCandidate(
                file_path=Path("/test/yt.md"),
                title="YouTube",
                current_prompt_version="v1",
                target_prompt_version="v2",
                source_url="https://youtube.com/watch?v=1",
                source_type="youtube",
            ),
            RefreshCandidate(
                file_path=Path("/test/web.md"),
                title="Web",
                current_prompt_version="v1",
                target_prompt_version="v2",
                source_url="https://example.com",
                source_type="web",
            ),
            RefreshCandidate(
                file_path=Path("/test/pdf.md"),
                title="PDF",
                current_prompt_version="v1",
                target_prompt_version="v2",
                source_url="https://example.com/doc.pdf",
                source_type="pdf",
            ),
        ]

        cost = estimate_refresh_cost(candidates)

        # youtube: 0.02, web: 0.01, pdf: 0.03 = 0.06
        assert cost == 0.06


class TestCreateBackup:
    """Tests for backup creation."""

    def test_create_backup(self, tmp_path: Path) -> None:
        """Create backup of a note."""
        note = tmp_path / "test.md"
        note.write_text("Original content", encoding="utf-8")

        backup_path = create_backup(note)

        assert backup_path.exists()
        assert backup_path.name == "test.backup.md"
        assert backup_path.read_text() == "Original content"
        assert note.exists()  # Original still exists

    def test_create_backup_existing_adds_timestamp(self, tmp_path: Path) -> None:
        """Create timestamped backup when backup exists."""
        note = tmp_path / "test.md"
        note.write_text("Original", encoding="utf-8")

        existing_backup = tmp_path / "test.backup.md"
        existing_backup.write_text("Old backup", encoding="utf-8")

        backup_path = create_backup(note)

        # Should have timestamp suffix
        assert backup_path.exists()
        assert backup_path != existing_backup
        assert "backup_" in backup_path.name
        assert existing_backup.read_text() == "Old backup"  # Old backup unchanged


class TestRefreshModels:
    """Tests for Pydantic models."""

    def test_refresh_candidate_model(self) -> None:
        """RefreshCandidate model validation."""
        candidate = RefreshCandidate(
            file_path=Path("/test/note.md"),
            title="Test Note",
            current_prompt_version="youtube_v1",
            target_prompt_version="youtube_v2",
            source_url="https://youtube.com/watch?v=abc",
            source_type="youtube",
        )

        assert candidate.file_path == Path("/test/note.md")
        assert candidate.title == "Test Note"

    def test_refresh_result_model(self) -> None:
        """RefreshResult model validation."""
        result = RefreshResult(
            file_path=Path("/test/note.md"),
            success=True,
            backup_path=Path("/test/note.backup.md"),
            cost_usd=0.02,
        )

        assert result.success is True
        assert result.error is None

    def test_refresh_summary_model(self) -> None:
        """RefreshSummary model validation."""
        summary = RefreshSummary(
            total_candidates=10,
            refreshed=8,
            skipped=2,
            errors=["Error 1", "Error 2"],
            total_cost_usd=0.16,
        )

        assert summary.total_candidates == 10
        assert len(summary.errors) == 2


def _candidate(tmp_path: Path) -> RefreshCandidate:
    note = tmp_path / "inbox" / "note.md"
    note.parent.mkdir(exist_ok=True)
    note.write_text("original", encoding="utf-8")
    return RefreshCandidate(
        file_path=note,
        title="Note",
        current_prompt_version="youtube_v1",
        target_prompt_version="youtube_v2",
        source_url="https://youtube.com/watch?v=abc",
        source_type="youtube",
    )


class TestRefreshNote:
    """Tests for the destructive single-note refresh workflow."""

    def test_refresh_note_success_creates_backup_and_writes_same_folder(
        self, tmp_path: Path
    ) -> None:
        """A refresh should preserve the original and write into its current folder."""
        candidate = _candidate(tmp_path)
        provider = MagicMock()
        provider.ingest.return_value = MagicMock()
        generated_note = MagicMock()

        with (
            patch(
                "obsidian_ai_tools.providers.factory.ProviderFactory.get_provider",
                return_value=provider,
            ),
            patch("obsidian_ai_tools.llm.generate_note", return_value=generated_note),
            patch("obsidian_ai_tools.obsidian.write_note") as write_note,
        ):
            result = refresh_note(candidate, tmp_path, "model", "key")

        assert result.success is True
        assert result.backup_path == tmp_path / "inbox" / "note.backup.md"
        assert result.backup_path.read_text(encoding="utf-8") == "original"
        assert result.cost_usd == 0.02
        write_note.assert_called_once_with(generated_note, tmp_path, "inbox")

    def test_refresh_note_reports_source_failure(self, tmp_path: Path) -> None:
        """Source fetch failures should return a skipped refresh result."""
        candidate = _candidate(tmp_path)

        with patch(
            "obsidian_ai_tools.providers.factory.ProviderFactory.get_provider",
            side_effect=RuntimeError("offline"),
        ):
            result = refresh_note(candidate, tmp_path, "model", "key")

        assert result.success is False
        assert "Source unavailable" in str(result.error)
        assert result.backup_path is not None

    def test_refresh_note_reports_generation_failure(self, tmp_path: Path) -> None:
        """LLM failures should not overwrite the original note."""
        from obsidian_ai_tools.llm import NoteGenerationError

        candidate = _candidate(tmp_path)
        provider = MagicMock()

        with (
            patch(
                "obsidian_ai_tools.providers.factory.ProviderFactory.get_provider",
                return_value=provider,
            ),
            patch(
                "obsidian_ai_tools.llm.generate_note",
                side_effect=NoteGenerationError("bad response"),
            ),
        ):
            result = refresh_note(candidate, tmp_path, "model", "key", create_backup_file=False)

        assert result.success is False
        assert result.backup_path is None
        assert "LLM generation failed" in str(result.error)

    def test_refresh_note_reports_unexpected_write_failure(self, tmp_path: Path) -> None:
        """Unexpected write errors should be returned instead of escaping."""
        candidate = _candidate(tmp_path)
        provider = MagicMock()

        with (
            patch(
                "obsidian_ai_tools.providers.factory.ProviderFactory.get_provider",
                return_value=provider,
            ),
            patch("obsidian_ai_tools.llm.generate_note", return_value=MagicMock()),
            patch("obsidian_ai_tools.obsidian.write_note", side_effect=OSError("disk full")),
        ):
            result = refresh_note(candidate, tmp_path, "model", "key", create_backup_file=False)

        assert result.success is False
        assert "Unexpected error: disk full" == result.error


class TestRefreshBatch:
    """Tests for aggregation of batch refresh results."""

    def test_refresh_batch_aggregates_successes_and_errors(self, tmp_path: Path) -> None:
        """Batch summaries should count refreshed and skipped notes."""
        first = _candidate(tmp_path)
        second = first.model_copy(update={"file_path": tmp_path / "second.md"})

        with patch(
            "obsidian_ai_tools.refresh.refresh_note",
            side_effect=[
                RefreshResult(file_path=first.file_path, success=True, cost_usd=0.02),
                RefreshResult(file_path=second.file_path, success=False, error="offline"),
            ],
        ):
            summary = refresh_batch([first, second], tmp_path, "model", "key")

        assert summary.total_candidates == 2
        assert summary.refreshed == 1
        assert summary.skipped == 1
        assert summary.total_cost_usd == 0.02
        assert summary.errors == ["second.md: offline"]
