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
    FolderOrganizerError,
    InvalidRulesError,
    MoveResult,
    NoteToMove,
    PathTraversalError,
    _collect_existing_folders,
    _create_move_result,
    _find_existing_folder_match,
    _format_tag_segment_as_folder,
    _tokenize_rule_text,
    calculate_folder_scores,
    find_best_folder,
    load_folder_rules,
    move_note,
    normalize_tags,
    scan_inbox_notes,
    suggest_folder_for_tag,
    track_move,
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


class TestCreateMoveResult:
    """Tests for internal MoveResult factory."""

    def _note_in_vault(self, vault: Path, tags: list[str] | None = None) -> NoteToMove:
        """Create a note located inside the vault inbox."""
        inbox = vault / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        source = inbox / "note.md"
        source.write_text("# Content", encoding="utf-8")
        tags = ["ai"] if tags is None else tags
        return NoteToMove(
            file_path=source,
            title="Note",
            tags=list(tags),
            best_folder="AI/Projects",
            matched_tags=list(tags),
            score=1.5,
        )

    def test_create_move_result_in_vault(self, tmp_path: Path) -> None:
        """Map all note fields onto the MoveResult."""
        vault = tmp_path / "vault"
        vault.mkdir()
        note = self._note_in_vault(vault)

        result = _create_move_result(note, vault, success=True)

        assert result.file == "note.md"
        assert result.from_folder == "inbox"
        assert result.to_folder == "AI/Projects"
        assert result.tags == ["ai"]
        assert result.matched_tag == "ai"
        assert result.score == 1.5
        assert result.success is True
        assert result.error is None
        assert result.timestamp is not None

    def test_create_move_result_outside_vault(self, tmp_path: Path) -> None:
        """Fall back to parent folder name when note is outside vault."""
        vault = tmp_path / "vault"
        vault.mkdir()
        source = tmp_path / "elsewhere" / "note.md"
        source.parent.mkdir()
        source.write_text("# Content", encoding="utf-8")
        note = NoteToMove(file_path=source, title="Note", best_folder="Target")

        result = _create_move_result(note, vault, success=False, error="boom")

        assert result.from_folder == "elsewhere"
        assert result.to_folder == "Target"
        assert result.success is False
        assert result.error == "boom"

    def test_create_move_result_without_matched_tags(self, tmp_path: Path) -> None:
        """matched_tag is None when no tags matched."""
        vault = tmp_path / "vault"
        vault.mkdir()
        note = self._note_in_vault(vault, tags=[])

        result = _create_move_result(note, vault, success=True)

        assert result.matched_tag is None
        assert result.tags == []


class TestMoveNoteSecurity:
    """Tests for move_note failure branches and defaults."""

    def _make_note(self, vault: Path, best_folder: str | None) -> NoteToMove:
        """Create a note file in the vault inbox."""
        inbox = vault / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        source = inbox / "note.md"
        source.write_text("# Content", encoding="utf-8")
        return NoteToMove(
            file_path=source,
            title="Test Note",
            tags=["ai"],
            best_folder=best_folder,
            matched_tags=["ai"] if best_folder else [],
            score=1.0,
        )

    def test_move_note_default_is_not_dry_run(self, tmp_path: Path) -> None:
        """The default dry_run=False actually performs the move."""
        vault = tmp_path / "vault"
        vault.mkdir()
        note = self._make_note(vault, "AI/Projects")

        result = move_note(note, vault)

        assert result.success
        assert not note.file_path.exists()
        assert (vault / "AI" / "Projects" / "note.md").exists()

    def test_move_note_without_best_folder(self, tmp_path: Path) -> None:
        """Return a failure result when no target folder was determined."""
        vault = tmp_path / "vault"
        vault.mkdir()
        note = self._make_note(vault, None)

        result = move_note(note, vault)

        assert result.success is False
        assert result.error == "No target folder determined"
        assert result.file == "note.md"
        assert result.to_folder == ""

    def test_move_note_detects_path_traversal(self, tmp_path: Path) -> None:
        """Reject destinations that resolve outside the vault."""
        vault = tmp_path / "vault"
        vault.mkdir()
        note = self._make_note(vault, "../escape")

        result = move_note(note, vault)

        assert result.success is False
        assert result.error == "Path traversal detected"
        assert result.file == "note.md"
        assert result.to_folder == "../escape"
        assert note.file_path.exists()

    def test_move_note_wraps_path_validation_error(self, tmp_path: Path, monkeypatch) -> None:
        """Handle exceptions raised while resolving destinations."""
        vault = tmp_path / "vault"
        vault.mkdir()
        note = self._make_note(vault, "AI/Projects")

        def boom(self, strict: bool = False) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(Path, "resolve", boom)

        result = move_note(note, vault)

        assert result.success is False
        assert result.error == "Path validation failed: boom"
        assert note.file_path.exists()

    def test_move_note_accepts_existing_dest_folder(self, tmp_path: Path) -> None:
        """Succeed when the destination folder already exists."""
        vault = tmp_path / "vault"
        vault.mkdir()
        dest = vault / "AI" / "Projects"
        dest.mkdir(parents=True)
        note = self._make_note(vault, "AI/Projects")

        result = move_note(note, vault)

        assert result.success
        assert (dest / "note.md").exists()

    def test_move_note_wraps_filesystem_error(self, tmp_path: Path, monkeypatch) -> None:
        """Report failures during the actual move as errors."""
        vault = tmp_path / "vault"
        vault.mkdir()
        note = self._make_note(vault, "AI/Projects")

        def boom(self, *args, **kwargs) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(Path, "mkdir", boom)

        result = move_note(note, vault)

        assert result.success is False
        assert result.error == "boom"
        assert result.file == "note.md"
        assert result.to_folder == "AI/Projects"


class TestTrackMove:
    """Tests for move tracking in the JSONL log."""

    def _result(self) -> MoveResult:
        """Build a representative successful move result."""
        return MoveResult(
            file="note.md",
            from_folder="inbox",
            to_folder="AI/Projects",
            tags=["ai"],
            matched_tag="ai",
            score=1.0,
            success=True,
            error=None,
        )

    def test_track_move_writes_jsonl(self, tmp_path: Path) -> None:
        """Write a single JSON line with the full field set."""
        vault = tmp_path / "vault"
        vault.mkdir()

        track_move(self._result(), vault)

        tracking_file = vault / ".kai" / "folder_mappings.jsonl"
        assert tracking_file.exists()
        content = tracking_file.read_text(encoding="utf-8")
        assert content.endswith("\n")
        lines = [line for line in content.splitlines() if line]
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert set(data) == {
            "file",
            "from",
            "to",
            "timestamp",
            "tags",
            "matched_tag",
            "score",
            "success",
            "error",
        }
        assert data["file"] == "note.md"
        assert data["from"] == "inbox"
        assert data["to"] == "AI/Projects"
        assert data["tags"] == ["ai"]
        assert data["matched_tag"] == "ai"
        assert data["score"] == 1.0
        assert data["success"] is True
        assert data["error"] is None
        assert data["timestamp"]

    def test_track_move_appends_entries(self, tmp_path: Path) -> None:
        """Append a new JSON line per tracked move."""
        vault = tmp_path / "vault"
        vault.mkdir()

        track_move(self._result(), vault)
        track_move(self._result(), vault)

        tracking_file = vault / ".kai" / "folder_mappings.jsonl"
        lines = [line for line in tracking_file.read_text().splitlines() if line]
        assert len(lines) == 2

    def test_track_move_creates_kai_recursively(self, tmp_path: Path) -> None:
        """Create missing vault and .kai directories during tracking."""
        vault = tmp_path / "nested" / "vault"

        track_move(self._result(), vault)

        tracking_file = vault / ".kai" / "folder_mappings.jsonl"
        assert tracking_file.exists()

    def test_track_move_raises_when_kai_is_a_file(self, tmp_path: Path) -> None:
        """Wrap tracking failures in FolderOrganizerError."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".kai").write_text("blocked", encoding="utf-8")

        with pytest.raises(FolderOrganizerError) as exc_info:
            track_move(self._result(), vault)

        assert str(exc_info.value).startswith("Failed to track move:")
        assert str(exc_info.value) != "None"


class TestCollectExistingFolders:
    """Tests for collecting candidate folders."""

    def _build_vault(self, tmp_path: Path) -> Path:
        """Create a vault with mixed visible, hidden and deep folders."""
        vault = tmp_path / "vault"
        (vault / "Notes").mkdir(parents=True)
        (vault / "AI" / "Projects").mkdir(parents=True)
        (vault / "Deep" / "A" / "B" / "C").mkdir(parents=True)
        (vault / "Level5" / "A" / "B" / "C" / "D").mkdir(parents=True)
        (vault / ".git").mkdir()
        (vault / ".obsidian").mkdir()
        (vault / ".kai").mkdir()
        (vault / ".trash").mkdir()
        (vault / ".custom").mkdir()
        (vault / "scratch.txt").write_text("x", encoding="utf-8")
        return vault

    def test_collect_rules_and_dirs(self, tmp_path: Path) -> None:
        """Include rule folders, visible dirs; skip hidden and deep dirs."""
        vault = self._build_vault(tmp_path)

        folders = _collect_existing_folders(vault, {"x": "RulesFolder"})

        assert "RulesFolder" in folders
        assert "Notes" in folders
        assert "AI/Projects" in folders
        assert "Deep/A/B/C" in folders
        assert "Level5/A/B/C/D" not in folders
        assert ".git" not in folders
        assert ".obsidian" not in folders
        assert ".kai" not in folders
        assert ".trash" not in folders
        assert ".custom" not in folders
        assert "scratch.txt" not in folders

    def test_collect_sorted_and_without_files(self, tmp_path: Path) -> None:
        """Return a sorted list even when only rules exist."""
        vault = tmp_path / "vault"
        vault.mkdir()

        folders = _collect_existing_folders(vault, {"a": "Zeta", "b": "Alpha"})

        assert folders == ["Alpha", "Zeta"]

    def test_collect_break_on_first_file(self, tmp_path: Path, monkeypatch) -> None:
        """Non-directory entries must not stop collection."""
        vault = tmp_path / "vault"
        (vault / "Notes").mkdir(parents=True)
        (vault / "scratch.txt").write_text("x", encoding="utf-8")
        monkeypatch.setattr(
            Path,
            "rglob",
            lambda self, pattern: iter([vault / "scratch.txt", vault / "Notes"]),
        )

        folders = _collect_existing_folders(vault, {})

        assert "Notes" in folders

    def test_collect_break_on_hidden_dir(self, tmp_path: Path, monkeypatch) -> None:
        """Hidden directories must not stop collection."""
        vault = tmp_path / "vault"
        (vault / "Notes").mkdir(parents=True)
        (vault / ".custom").mkdir()
        monkeypatch.setattr(
            Path, "rglob", lambda self, pattern: iter([vault / ".custom", vault / "Notes"])
        )

        folders = _collect_existing_folders(vault, {})

        assert "Notes" in folders

    def test_collect_break_on_too_deep_dir(self, tmp_path: Path, monkeypatch) -> None:
        """Deep directories must not stop collection."""
        vault = tmp_path / "vault"
        (vault / "Notes").mkdir(parents=True)
        deep = vault / "A" / "B" / "C" / "D" / "E"
        deep.mkdir(parents=True)
        monkeypatch.setattr(Path, "rglob", lambda self, pattern: iter([deep, vault / "Notes"]))

        folders = _collect_existing_folders(vault, {})

        assert "Notes" in folders


class TestFindExistingFolderMatch:
    """Tests for token-overlap matching against existing folders."""

    def test_matches_on_token_overlap(self) -> None:
        """Return the folder with the best token overlap."""
        result = _find_existing_folder_match("machine learning", ["Machine Learning"])
        assert result == "Machine Learning"

    def test_skips_tokenless_and_unrelated_folders(self) -> None:
        """Continue past folders without tokens or overlap."""
        folders = ["A", "Unrelated Folder", "Machine Learning"]
        result = _find_existing_folder_match("machine learning", folders)
        assert result == "Machine Learning"

    def test_exact_half_score_qualifies(self) -> None:
        """A score of exactly 0.5 meets the threshold."""
        result = _find_existing_folder_match("alpha beta", ["Alpha"])
        assert result == "Alpha"

    def test_below_threshold_returns_none(self) -> None:
        """Low token overlap without substring match returns None."""
        result = _find_existing_folder_match("alpha beta gamma zeta omega", ["Alpha Beta"])
        assert result is None

    def test_no_overlap_returns_none(self) -> None:
        """Folders sharing no tokens are ignored."""
        result = _find_existing_folder_match("alpha", ["beta"])
        assert result is None

    def test_hyphen_tag_prefers_space_folder(self) -> None:
        """Substring bonus favors the folder that contains the space form."""
        result = _find_existing_folder_match("ai-agents", ["AI Agents", "AI-Agents"])
        assert result == "AI Agents"

    def test_hyphen_tag_bonus_ranking(self) -> None:
        """Hyphen tags still award the substring bonus to the space folder."""
        result = _find_existing_folder_match("ai-agents", ["Agents AI", "AI-Agents"])
        assert result == "AI-Agents"

    def test_substring_bonus_ranking(self) -> None:
        """Substring bonus lifts the matching folder above tied competitors."""
        result = _find_existing_folder_match("ai agents", ["Agents AI", "AI-Agents"])
        assert result == "AI-Agents"

    def test_bonus_and_multi_tag_ranking(self) -> None:
        """Multi-token overlap plus bonus beats partial overlap."""
        folders = ["AI Agents", "AI Agents Guide"]
        result = _find_existing_folder_match("ai agents guide", folders)
        assert result == "AI Agents Guide"

    def test_tie_keeps_first_folder(self) -> None:
        """Strictly-greater comparison keeps the first tied folder."""
        result = _find_existing_folder_match("ai", ["X AI", "AI"])
        assert result == "X AI"


class TestFormatTagSegmentAsFolder:
    """Tests for tag segment formatting."""

    def test_uppercase_acronyms(self) -> None:
        """All known acronyms are uppercased."""
        assert _format_tag_segment_as_folder("ai") == "AI"
        assert _format_tag_segment_as_folder("api") == "API"
        assert _format_tag_segment_as_folder("ar") == "AR"
        assert _format_tag_segment_as_folder("eu") == "EU"
        assert _format_tag_segment_as_folder("hr") == "HR"
        assert _format_tag_segment_as_folder("it") == "IT"
        assert _format_tag_segment_as_folder("lgbtq") == "LGBTQ"
        assert _format_tag_segment_as_folder("llm") == "LLM"
        assert _format_tag_segment_as_folder("ml") == "ML"
        assert _format_tag_segment_as_folder("nlp") == "NLP"
        assert _format_tag_segment_as_folder("pdf") == "PDF"
        assert _format_tag_segment_as_folder("smr") == "SMR"
        assert _format_tag_segment_as_folder("ui") == "UI"
        assert _format_tag_segment_as_folder("ux") == "UX"
        assert _format_tag_segment_as_folder("vr") == "VR"

    def test_capitalizes_regular_words(self) -> None:
        """Non-acronym words are capitalized."""
        assert _format_tag_segment_as_folder("machine") == "Machine"
        assert _format_tag_segment_as_folder("deep-learning-models") == "Deep Learning Models"

    def test_joins_words_with_spaces(self) -> None:
        """Spaces, underscores and hyphens all become word separators."""
        assert _format_tag_segment_as_folder("machine learning") == "Machine Learning"
        assert _format_tag_segment_as_folder("machine_learning") == "Machine Learning"
        assert _format_tag_segment_as_folder("machine-learning") == "Machine Learning"
        assert _format_tag_segment_as_folder("ai") == "AI"


class TestTokenizeRuleText:
    """Tests for lightweight tokenization."""

    def test_tokenize_splits_and_lowercases(self) -> None:
        """Split on non-alphanumerics and lowercase each token."""
        assert _tokenize_rule_text("Machine-Learning Models") == {
            "machine",
            "learning",
            "models",
        }

    def test_tokenize_skips_single_chars(self) -> None:
        """Tokens shorter than two characters are dropped."""
        assert _tokenize_rule_text("a bb ccc") == {"bb", "ccc"}

    def test_tokenize_splits_punctuation(self) -> None:
        """Punctuation separates tokens."""
        assert _tokenize_rule_text("foo,bar!baz") == {"foo", "bar", "baz"}


class TestSuggestExistingFolders:
    """Tests for folder suggestion edge cases."""

    def test_suggest_prefers_matching_existing_folder(self) -> None:
        """An existing folder match wins over formatting the tag."""
        assert suggest_folder_for_tag("machine learning", ["Machine"]) == "Machine"

    def test_suggest_strips_hash_prefix(self) -> None:
        """Leading hash characters are stripped."""
        assert suggest_folder_for_tag("#machine_learning") == "Machine Learning"

    def test_suggest_ignores_single_dot_segments(self) -> None:
        """Single-dot path segments are dropped."""
        assert suggest_folder_for_tag("./machine_learning") == "Machine Learning"

    def test_suggest_ignores_double_dot_segments(self) -> None:
        """Double-dot path segments are dropped."""
        assert suggest_folder_for_tag("machine_learning/..") == "Machine Learning"

    def test_suggest_dot_only_returns_uncategorized(self) -> None:
        """Tags made only of dot segments fall back to Uncategorized."""
        assert suggest_folder_for_tag("..") == "Uncategorized"

    def test_suggest_empty_returns_uncategorized(self) -> None:
        """Empty tags fall back to Uncategorized."""
        assert suggest_folder_for_tag("") == "Uncategorized"
        assert suggest_folder_for_tag("#/") == "Uncategorized"


class TestValidateFolderPathMessages:
    """Tests for exact error messages from path validation."""

    def test_exact_traversal_error_message(self, tmp_path: Path) -> None:
        """Path traversal raises with the documented message."""
        vault = tmp_path / "vault"
        vault.mkdir()

        with pytest.raises(PathTraversalError) as exc_info:
            validate_folder_path("../outside", vault)

        assert str(exc_info.value) == (
            "Folder path contains invalid sequences: ../outside. "
            "Paths cannot contain '..', or start with '/' or '\\'."
        )

    def test_exact_absolute_path_message(self, tmp_path: Path) -> None:
        """Leading slashes raise with the documented message."""
        vault = tmp_path / "vault"
        vault.mkdir()

        with pytest.raises(PathTraversalError) as exc_info:
            validate_folder_path("/Notes", vault)

        assert str(exc_info.value) == (
            "Folder path contains invalid sequences: /Notes. "
            "Paths cannot contain '..', or start with '/' or '\\'."
        )

    def test_rejects_backslash_leading_path(self, tmp_path: Path) -> None:
        """Backslash-leading paths are rejected."""
        vault = tmp_path / "vault"
        vault.mkdir()

        with pytest.raises(PathTraversalError):
            validate_folder_path("\\Notes", vault)

    def test_default_max_depth(self, tmp_path: Path) -> None:
        """The default max depth of 4 rejects 5-level paths."""
        vault = tmp_path / "vault"
        vault.mkdir()

        with pytest.raises(InvalidRulesError):
            validate_folder_path("a/b/c/d/e", vault)

    def test_max_depth_boundary_allows_exact_max(self, tmp_path: Path) -> None:
        """A path with exactly max_depth levels is accepted."""
        vault = tmp_path / "vault"
        vault.mkdir()

        validate_folder_path("a/b/c/d", vault, max_depth=4)

    def test_exact_depth_error_message(self, tmp_path: Path) -> None:
        """Depth violations report the level counts."""
        vault = tmp_path / "vault"
        vault.mkdir()

        with pytest.raises(InvalidRulesError) as exc_info:
            validate_folder_path("a/b/c/d/e", vault, max_depth=4)

        assert str(exc_info.value) == "Folder path 'a/b/c/d/e' has 5 levels, maximum is 4"

    def test_escapes_vault_error_message(self, tmp_path: Path) -> None:
        """Symlink escapes are reported with the vault context."""
        vault = tmp_path / "vault"
        vault.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (vault / "link").symlink_to(outside, target_is_directory=True)

        with pytest.raises(PathTraversalError) as exc_info:
            validate_folder_path("link/secret", vault)

        message = str(exc_info.value)
        assert message.startswith(
            "Failed to validate folder path 'link/secret': Folder path escapes vault: link/secret"
        )
        assert "resolves outside" in message

    def test_resolve_failure_exact_message(self, tmp_path: Path, monkeypatch) -> None:
        """Resolve failures are wrapped with the underlying error."""
        vault = tmp_path / "vault"
        vault.mkdir()

        def boom(self, strict: bool = False) -> None:
            raise OSError("boom")

        monkeypatch.setattr(Path, "resolve", boom)

        with pytest.raises(PathTraversalError) as exc_info:
            validate_folder_path("Notes", vault)

        assert str(exc_info.value) == "Failed to validate folder path 'Notes': boom"


class TestLoadFolderRulesMessages:
    """Tests for exact error messages from rule loading."""

    def test_missing_file_exact_message(self, tmp_path: Path) -> None:
        """Missing rules file reports its full path."""
        vault = tmp_path / "vault"
        vault.mkdir()

        with pytest.raises(InvalidRulesError) as exc_info:
            load_folder_rules(vault)

        assert str(exc_info.value) == (
            f"No folder_rules.json found at {vault / 'folder_rules.json'}. "
            "Create this file in your vault root with tag-to-folder mappings."
        )

    def test_non_dict_rules_exact_message(self, tmp_path: Path) -> None:
        """Non-object JSON rules are rejected with the documented message."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "folder_rules.json").write_text("[]", encoding="utf-8")

        with pytest.raises(InvalidRulesError) as exc_info:
            load_folder_rules(vault)

        assert str(exc_info.value) == "folder_rules.json must contain a JSON object (dict)"

    def test_invalid_json_includes_error(self, tmp_path: Path) -> None:
        """JSON decode errors are wrapped with context."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "folder_rules.json").write_text("not valid json", encoding="utf-8")

        with pytest.raises(InvalidRulesError) as exc_info:
            load_folder_rules(vault)

        assert "Invalid JSON in folder_rules.json" in str(exc_info.value)


class TestCalculateFolderScoresExact:
    """Tests for exact scoring arithmetic."""

    def test_exact_first_match_score(self) -> None:
        """First match gets 1.0 plus 0.1 per path level."""
        scores = calculate_folder_scores(["ai"], {"ai": "AI/Sub"})
        assert scores == {"AI/Sub": (1.1, ["ai"])}

    def test_exact_accumulated_score(self) -> None:
        """Additional matching tags accumulate 1.0 each."""
        rules = {"ai": "AI/Projects", "ml": "AI/Projects"}
        scores = calculate_folder_scores(["ai", "ml"], rules)
        assert scores["AI/Projects"][0] == 2.1
        assert scores["AI/Projects"][1] == ["ai", "ml"]


class TestFindBestFolderEmpty:
    """Tests for find_best_folder edge cases."""

    def test_empty_tags_return_no_match(self) -> None:
        """Empty tag lists produce a degenerate result."""
        folder, matched, score = find_best_folder([], {"ai": "AI"})
        assert folder is None
        assert matched == []
        assert score == 0.0
