"""Tests for folder_organizer module.

Test Strategy:
- Unit tests for individual functions (validate_folder_path, scan_inbox_notes)
- Integration tests for complete organization workflows
- Edge cases for conflict resolution, empty vaults, and invalid rules
"""

import json
from pathlib import Path

import pytest

from obsidian_ai_tools.folder_organizer import (
    InvalidRulesError,
    MoveResult,
    NoteToMove,
    PathTraversalError,
    calculate_folder_scores,
    find_best_folder,
    load_folder_rules,
    move_note,
    normalize_tags,
    scan_inbox_notes,
    suggest_folder_for_tag,
    suggest_folder_rules,
    update_folder_rules,
    validate_folder_path,
)


class TestValidateFolderPath:
    """Tests for path validation and security."""

    def test_valid_folder_path(self, tmp_path: Path) -> None:
        """Accept valid folder paths within vault."""
        vault = tmp_path / "vault"
        vault.mkdir()

        # Should not raise
        validate_folder_path("AI/Projects", vault)
        validate_folder_path("Notes", vault)
        validate_folder_path("Deep/Nested/Folder", vault)

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        """Reject paths with traversal sequences."""
        vault = tmp_path / "vault"
        vault.mkdir()

        with pytest.raises(PathTraversalError):
            validate_folder_path("../outside", vault)

        with pytest.raises(PathTraversalError):
            validate_folder_path("AI/../../../etc", vault)

    def test_rejects_absolute_paths(self, tmp_path: Path) -> None:
        """Reject absolute paths."""
        vault = tmp_path / "vault"
        vault.mkdir()

        with pytest.raises(PathTraversalError):
            validate_folder_path("/etc/passwd", vault)

    def test_rejects_too_deep_paths(self, tmp_path: Path) -> None:
        """Reject paths that exceed maximum depth."""
        vault = tmp_path / "vault"
        vault.mkdir()

        with pytest.raises(InvalidRulesError):
            validate_folder_path("a/b/c/d/e", vault, max_depth=4)


class TestLoadFolderRules:
    """Tests for loading and validating folder rules."""

    def test_load_valid_rules(self, tmp_path: Path) -> None:
        """Load valid rules from JSON file."""
        vault = tmp_path / "vault"
        vault.mkdir()

        rules = {"ai": "AI/Projects", "python": "Programming/Python"}
        (vault / "folder_rules.json").write_text(json.dumps(rules))

        loaded = load_folder_rules(vault)

        assert loaded == rules

    def test_missing_rules_file_raises(self, tmp_path: Path) -> None:
        """Raise error when rules file doesn't exist."""
        vault = tmp_path / "vault"
        vault.mkdir()

        with pytest.raises(InvalidRulesError) as exc_info:
            load_folder_rules(vault)

        assert "folder_rules.json" in str(exc_info.value)

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        """Raise error for invalid JSON."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "folder_rules.json").write_text("not valid json")

        with pytest.raises(InvalidRulesError):
            load_folder_rules(vault)

    def test_rules_with_unsafe_paths_raises(self, tmp_path: Path) -> None:
        """Raise error when rules contain unsafe paths."""
        vault = tmp_path / "vault"
        vault.mkdir()

        rules = {"ai": "../outside/vault"}
        (vault / "folder_rules.json").write_text(json.dumps(rules))

        with pytest.raises(PathTraversalError):
            load_folder_rules(vault)


class TestNormalizeTags:
    """Tests for tag normalization."""

    def test_normalize_list(self) -> None:
        """Pass through list of tags."""
        result = normalize_tags(["ai", "python"])
        assert result == ["ai", "python"]

    def test_normalize_string(self) -> None:
        """Convert single string tag to list."""
        result = normalize_tags("ai")
        assert result == ["ai"]

    def test_normalize_none(self) -> None:
        """Return empty list for None."""
        result = normalize_tags(None)
        assert result == []


class TestCalculateFolderScores:
    """Tests for folder scoring algorithm."""

    def test_single_tag_match(self) -> None:
        """Score folder with single matching tag."""
        rules = {"ai": "AI/Projects", "python": "Programming"}
        scores = calculate_folder_scores(["ai"], rules)

        assert "AI/Projects" in scores
        assert scores["AI/Projects"][0] > 0

    def test_multiple_tag_matches(self) -> None:
        """Higher score for multiple matching tags."""
        rules = {"ai": "AI/Projects", "ml": "AI/Projects", "python": "Programming"}
        scores = calculate_folder_scores(["ai", "ml"], rules)

        # AI/Projects should have higher score (2 matches)
        assert scores["AI/Projects"][0] > scores.get("Programming", (0, []))[0]

    def test_no_matches(self) -> None:
        """Empty scores when no tags match."""
        rules = {"ai": "AI/Projects"}
        scores = calculate_folder_scores(["javascript"], rules)

        assert len(scores) == 0

    def test_deeper_paths_get_bonus(self) -> None:
        """Deeper folder paths get specificity bonus."""
        rules = {"ai": "AI", "deep-ai": "AI/Projects/Advanced"}

        # Both tags match their respective folders
        scores_shallow = calculate_folder_scores(["ai"], rules)
        scores_deep = calculate_folder_scores(["deep-ai"], rules)

        # Deeper path should have higher score due to specificity bonus
        # (each level adds +0.1)
        assert scores_deep["AI/Projects/Advanced"][0] > scores_shallow["AI"][0]


class TestFindBestFolder:
    """Tests for best folder selection."""

    def test_finds_best_match(self) -> None:
        """Select folder with highest score."""
        rules = {"ai": "AI/Projects", "ml": "AI/Projects", "python": "Programming"}

        folder, matched, score = find_best_folder(["ai", "ml", "python"], rules)

        assert folder == "AI/Projects"
        assert set(matched) == {"ai", "ml"}

    def test_no_match_returns_none(self) -> None:
        """Return None when no tags match any rules."""
        rules = {"ai": "AI/Projects"}

        folder, matched, score = find_best_folder(["javascript"], rules)

        assert folder is None
        assert matched == []
        assert score == 0.0


class TestScanInboxNotes:
    """Tests for inbox scanning."""

    def test_scan_empty_inbox(self, tmp_path: Path) -> None:
        """Handle empty inbox gracefully."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "inbox").mkdir()

        rules = {"ai": "AI/Projects"}

        notes, failed = scan_inbox_notes(vault, "inbox", rules)

        assert len(notes) == 0
        assert len(failed) == 0

    def test_scan_finds_matching_notes(self, tmp_path: Path) -> None:
        """Find notes with matching tags."""
        vault = tmp_path / "vault"
        vault.mkdir()
        inbox = vault / "inbox"
        inbox.mkdir()

        # Create note with matching tag
        (inbox / "ai_note.md").write_text(
            """---
title: AI Note
tags: [ai, python]
---
# Content
""",
            encoding="utf-8",
        )

        rules = {"ai": "AI/Projects"}

        notes, failed = scan_inbox_notes(vault, "inbox", rules)

        assert len(notes) == 1
        assert notes[0].title == "AI Note"
        assert notes[0].best_folder == "AI/Projects"

    def test_scan_skips_notes_without_matching_tags(self, tmp_path: Path) -> None:
        """Skip notes that don't match any rules."""
        vault = tmp_path / "vault"
        vault.mkdir()
        inbox = vault / "inbox"
        inbox.mkdir()

        (inbox / "unmatched.md").write_text(
            """---
title: JavaScript Note
tags: [javascript]
---
# Content
""",
            encoding="utf-8",
        )

        rules = {"ai": "AI/Projects"}

        notes, failed = scan_inbox_notes(vault, "inbox", rules)

        assert len(notes) == 0

    def test_scan_returns_empty_for_missing_inbox(self, tmp_path: Path) -> None:
        """Return empty lists when inbox folder doesn't exist."""
        vault = tmp_path / "vault"
        vault.mkdir()

        rules = {"ai": "AI/Projects"}

        notes, failed = scan_inbox_notes(vault, "nonexistent_inbox", rules)

        assert len(notes) == 0
        assert len(failed) == 0


class TestRuleSuggestions:
    """Tests for suggesting and updating folder rules."""

    def test_suggest_folder_for_tag_formats_readable_folder(self) -> None:
        """Convert tags to readable folder paths."""
        assert suggest_folder_for_tag("ai/llm-agents") == "AI/LLM Agents"
        assert suggest_folder_for_tag("machine_learning") == "Machine Learning"

    def test_suggest_folder_rules_only_uses_unmatched_inbox_notes(self, tmp_path: Path) -> None:
        """Suggest rules only for notes that cannot be processed by existing rules."""
        vault = tmp_path / "vault"
        vault.mkdir()
        inbox = vault / "inbox"
        inbox.mkdir()

        (inbox / "matched.md").write_text(
            """---
title: Matched Note
tags: [ai, agents]
---
# Content
""",
            encoding="utf-8",
        )
        (inbox / "unmatched.md").write_text(
            """---
title: Unmatched Note
tags: [python, llm-agents]
---
# Content
""",
            encoding="utf-8",
        )

        suggestions, failed = suggest_folder_rules(vault, "inbox", {"ai": "AI"}, min_notes=1)

        assert failed == []
        assert {suggestion.tag for suggestion in suggestions} == {"python", "llm-agents"}
        folders = {suggestion.tag: suggestion.folder for suggestion in suggestions}
        assert folders["python"] == "Python"
        assert folders["llm-agents"] == "LLM Agents"

    def test_suggest_folder_rules_defaults_to_recurring_tags(self, tmp_path: Path) -> None:
        """Only suggest tags used by multiple unprocessed notes by default."""
        vault = tmp_path / "vault"
        vault.mkdir()
        inbox = vault / "inbox"
        inbox.mkdir()

        (inbox / "one.md").write_text(
            """---
title: One
tags: [relationships, dating]
---
# Content
""",
            encoding="utf-8",
        )
        (inbox / "two.md").write_text(
            """---
title: Two
tags: [relationships, communication]
---
# Content
""",
            encoding="utf-8",
        )

        suggestions, failed = suggest_folder_rules(vault, "inbox", {})

        assert failed == []
        assert [suggestion.tag for suggestion in suggestions] == ["relationships"]

    def test_suggest_folder_rules_prefers_existing_folder(self, tmp_path: Path) -> None:
        """Use matching existing folders before suggesting a new folder name."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "Personal" / "Relationships").mkdir(parents=True)
        inbox = vault / "inbox"
        inbox.mkdir()

        for index in range(2):
            (inbox / f"note-{index}.md").write_text(
                f"""---
title: Relationship Note {index}
tags: [relationships]
---
# Content
""",
                encoding="utf-8",
            )

        suggestions, failed = suggest_folder_rules(vault, "inbox", {})

        assert failed == []
        assert len(suggestions) == 1
        assert suggestions[0].folder == "Personal/Relationships"
        assert suggestions[0].existing_folder_match

    def test_update_folder_rules_creates_rules_file(self, tmp_path: Path) -> None:
        """Create folder_rules.json when applying suggestions to a new vault."""
        vault = tmp_path / "vault"
        vault.mkdir()

        suggestions, _failed = suggest_folder_rules(vault, "inbox", {})
        assert suggestions == []

        from obsidian_ai_tools.folder_organizer import RuleSuggestion

        update_folder_rules(
            vault,
            [
                RuleSuggestion(
                    tag="python",
                    folder="Python",
                    note_count=1,
                    example_notes=["Python Note"],
                )
            ],
        )

        rules = json.loads((vault / "folder_rules.json").read_text(encoding="utf-8"))
        assert rules == {"python": "Python"}

    def test_update_folder_rules_preserves_existing_rules(self, tmp_path: Path) -> None:
        """Do not overwrite existing tag mappings when applying suggestions."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "folder_rules.json").write_text(
            json.dumps({"ai": "Existing AI"}),
            encoding="utf-8",
        )

        from obsidian_ai_tools.folder_organizer import RuleSuggestion

        rules = update_folder_rules(
            vault,
            [
                RuleSuggestion(tag="ai", folder="AI", note_count=1, example_notes=["AI Note"]),
                RuleSuggestion(
                    tag="python",
                    folder="Python",
                    note_count=1,
                    example_notes=["Python Note"],
                ),
            ],
        )

        assert rules == {"ai": "Existing AI", "python": "Python"}


class TestMoveNote:
    """Tests for moving notes to destination folders."""

    def test_move_note_success(self, tmp_path: Path) -> None:
        """Successfully move note to destination folder."""
        vault = tmp_path / "vault"
        vault.mkdir()
        inbox = vault / "inbox"
        inbox.mkdir()

        # Create source note
        source = inbox / "note.md"
        source.write_text("# Content")

        note = NoteToMove(
            file_path=source,
            title="Test Note",
            tags=["ai"],
            best_folder="AI/Projects",
            matched_tags=["ai"],
            score=1.0,
        )

        result = move_note(note, vault, dry_run=False)

        assert result.success
        assert not source.exists()
        dest = vault / "AI" / "Projects" / "note.md"
        assert dest.exists()
        assert dest.read_text() == "# Content"

    def test_move_note_dry_run(self, tmp_path: Path) -> None:
        """Dry run doesn't actually move files."""
        vault = tmp_path / "vault"
        vault.mkdir()
        inbox = vault / "inbox"
        inbox.mkdir()

        source = inbox / "note.md"
        source.write_text("# Content")

        note = NoteToMove(
            file_path=source,
            title="Test Note",
            tags=["ai"],
            best_folder="AI/Projects",
            matched_tags=["ai"],
            score=1.0,
        )

        result = move_note(note, vault, dry_run=True)

        assert result.success
        assert source.exists()  # Not moved
        assert not (vault / "AI" / "Projects" / "note.md").exists()

    def test_move_note_handles_existing_file(self, tmp_path: Path) -> None:
        """Handle collision when destination file exists."""
        vault = tmp_path / "vault"
        vault.mkdir()
        inbox = vault / "inbox"
        inbox.mkdir()

        # Create destination folder and file
        dest_folder = vault / "AI" / "Projects"
        dest_folder.mkdir(parents=True)
        (dest_folder / "note.md").write_text("Existing content")

        source = inbox / "note.md"
        source.write_text("New content")

        note = NoteToMove(
            file_path=source,
            title="Test Note",
            tags=["ai"],
            best_folder="AI/Projects",
            matched_tags=["ai"],
            score=1.0,
        )

        result = move_note(note, vault, dry_run=False)

        # Should either fail or create unique name
        if result.success:
            # Source should be moved
            assert not source.exists()
        else:
            # Error should be reported
            assert result.error is not None


class TestMoveResult:
    """Tests for MoveResult model."""

    def test_move_result_success(self) -> None:
        """Create successful move result."""
        result = MoveResult(
            file="test.md",
            from_folder="inbox",
            to_folder="AI/Projects",
            matched_tag="ai",
            score=1.5,
            success=True,
        )

        assert result.success
        assert result.error is None

    def test_move_result_failure(self) -> None:
        """Create failed move result."""
        result = MoveResult(
            file="test.md",
            from_folder="inbox",
            to_folder="AI/Projects",
            matched_tag="ai",
            score=1.0,
            success=False,
            error="Permission denied",
        )

        assert not result.success
        assert "Permission" in str(result.error)
