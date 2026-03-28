"""Tests for tag hygiene analysis and consolidation."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from obsidian_ai_tools.indexer import NoteMetadata, VaultIndex
from obsidian_ai_tools.tag_hygiene import (
    SimilarTagGroup,
    TagConsolidation,
    TagCooccurrence,
    TagHygienePlan,
    _get_word_stems,
    _tags_share_semantic_root,
    analyze_cooccurrence,
    apply_consolidation,
    apply_plan,
    calculate_similarity,
    create_backup,
    find_orphan_tags,
    find_similar_tags,
    generate_plan,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_vault_index(tmp_path: Path) -> VaultIndex:
    """Create a sample vault index for testing."""
    notes = [
        NoteMetadata(
            file_path=tmp_path / "note1.md",
            title="Note about neurodivergence",
            tags=["neurodivergent", "mental-health", "autism"],
            content="Content about neurodivergent topics",
            modified_time=datetime.now().timestamp(),
        ),
        NoteMetadata(
            file_path=tmp_path / "note2.md",
            title="Note about neurodiversity",
            tags=["neurodiversity", "mental-health"],
            content="Content about neurodiversity",
            modified_time=datetime.now().timestamp(),
        ),
        NoteMetadata(
            file_path=tmp_path / "note3.md",
            title="Systems thinking note",
            tags=["systems", "systems-thinking", "mental-health"],
            content="Content about systems",
            modified_time=datetime.now().timestamp(),
        ),
        NoteMetadata(
            file_path=tmp_path / "note4.md",
            title="Autism research",
            tags=["autism", "mental-health", "research"],
            content="Research on autism",
            modified_time=datetime.now().timestamp(),
        ),
        NoteMetadata(
            file_path=tmp_path / "note5.md",
            title="Programming guide",
            tags=["programming", "ai"],
            content="How to program",
            modified_time=datetime.now().timestamp(),
        ),
        NoteMetadata(
            file_path=tmp_path / "note6.md",
            title="AI development",
            tags=["ai", "llm"],
            content="AI and LLM development",
            modified_time=datetime.now().timestamp(),
        ),
        NoteMetadata(
            file_path=tmp_path / "note7.md",
            title="Orphan tag note",
            tags=["unique-orphan-tag"],
            content="Note with unique tag",
            modified_time=datetime.now().timestamp(),
        ),
    ]

    index_path = tmp_path / ".kai" / "vault_index.json"
    return VaultIndex(notes=notes, index_path=index_path)


@pytest.fixture
def sample_note_file(tmp_path: Path) -> Path:
    """Create a sample note file with frontmatter."""
    note_path = tmp_path / "test_note.md"
    content = """---
title: Test Note
tags:
  - neurodivergence
  - mental-health
  - autism
created: 2026-01-01
---

# Test Note

This is test content.
"""
    note_path.write_text(content, encoding="utf-8")
    return note_path


# =============================================================================
# Unit Tests: Similarity Calculation
# =============================================================================


class TestCalculateSimilarity:
    """Tests for calculate_similarity function."""

    def test_identical_tags(self) -> None:
        """Identical tags should have similarity 1.0."""
        assert calculate_similarity("neurodivergent", "neurodivergent") == 1.0

    def test_similar_tags(self) -> None:
        """Similar tags should have high similarity score."""
        score = calculate_similarity("neurodivergent", "neurodivergence")
        assert score > 0.8

    def test_different_tags(self) -> None:
        """Very different tags should have low similarity."""
        score = calculate_similarity("neurodivergent", "programming")
        assert score < 0.5

    def test_case_insensitive(self) -> None:
        """Similarity should be case-insensitive."""
        assert calculate_similarity("AI", "ai") == 1.0
        assert calculate_similarity("Programming", "programming") == 1.0


# =============================================================================
# Unit Tests: Semantic Root Checking
# =============================================================================


class TestSemanticRootChecking:
    """Tests for semantic root comparison functions."""

    def test_get_word_stems_simple_tag(self) -> None:
        """Simple tags should return single stem."""
        stems = _get_word_stems("programming")
        assert "progr" in stems

    def test_get_word_stems_compound_tag(self) -> None:
        """Compound tags should return multiple stems."""
        stems = _get_word_stems("ai-development")
        assert "devel" in stems
        assert "ai" in stems

    def test_get_word_stems_handles_underscores(self) -> None:
        """Should treat underscores like hyphens."""
        stems = _get_word_stems("ai_development")
        assert "devel" in stems

    def test_semantic_root_matching_shared_prefix(self) -> None:
        """Tags with shared 5-char prefix should match."""
        assert _tags_share_semantic_root("neurodivergent", "neurodivergence")
        assert _tags_share_semantic_root("programming", "programmer")

    def test_semantic_root_no_match_different_words(self) -> None:
        """Tags with different word stems should not match."""
        # These have high character similarity but different meanings
        assert not _tags_share_semantic_root("ai-development", "ui-development")
        assert not _tags_share_semantic_root("software-development", "web-development")
        assert not _tags_share_semantic_root("project-management", "product-management")

    def test_semantic_root_matching_compound_with_shared_stem(self) -> None:
        """Compound tags with shared stems should match."""
        assert _tags_share_semantic_root("socio-technical", "sociotechnical")
        # Both have 'devel' stem
        assert _tags_share_semantic_root("ai-development", "development")

    def test_semantic_root_short_tags(self) -> None:
        """Short tags should require longer shared prefix."""
        # 'ai' and 'api' should not match (too short, no shared 5-char prefix)
        assert not _tags_share_semantic_root("ai", "api")


# =============================================================================
# Unit Tests: Find Similar Tags
# =============================================================================


class TestFindSimilarTags:
    """Tests for find_similar_tags function."""

    def test_finds_similar_tags(self, sample_vault_index: VaultIndex) -> None:
        """Should find groups of similar tags."""
        groups = find_similar_tags(sample_vault_index, threshold=0.75)

        # Should find neurodivergent/neurodiversity as similar
        all_variants = set()
        for g in groups:
            all_variants.update(g.variants)
            all_variants.add(g.canonical)

        # At least one of neurodivergent/neurodiversity should be grouped
        assert "neurodivergent" in all_variants or "neurodiversity" in all_variants

    def test_respects_threshold(self, sample_vault_index: VaultIndex) -> None:
        """Higher threshold should find fewer groups."""
        groups_low = find_similar_tags(sample_vault_index, threshold=0.5)
        groups_high = find_similar_tags(sample_vault_index, threshold=0.95)

        assert len(groups_low) >= len(groups_high)

    def test_empty_vault(self, tmp_path: Path) -> None:
        """Empty vault should return no groups."""
        index = VaultIndex(
            notes=[],
            index_path=tmp_path / ".kai" / "index.json",
        )
        groups = find_similar_tags(index)
        assert groups == []

    def test_excludes_substring_matches(self, tmp_path: Path) -> None:
        """Should not match tags where one is substring of another."""
        notes = [
            NoteMetadata(
                file_path=tmp_path / "note1.md",
                title="AI note",
                tags=["ai"],
                content="About AI",
                modified_time=datetime.now().timestamp(),
            ),
            NoteMetadata(
                file_path=tmp_path / "note2.md",
                title="AI development",
                tags=["ai-development"],
                content="AI development",
                modified_time=datetime.now().timestamp(),
            ),
        ]
        index = VaultIndex(notes=notes, index_path=tmp_path / ".kai" / "index.json")

        groups = find_similar_tags(index, threshold=0.5)

        # ai and ai-development should NOT be grouped (substring)
        for group in groups:
            all_tags = [group.canonical] + group.variants
            if "ai" in all_tags:
                assert "ai-development" not in all_tags


# =============================================================================
# Unit Tests: Co-occurrence Analysis
# =============================================================================


class TestAnalyzeCooccurrence:
    """Tests for analyze_cooccurrence function."""

    def test_finds_cooccurrence(self, sample_vault_index: VaultIndex) -> None:
        """Should find tags that frequently appear together."""
        coocs = analyze_cooccurrence(sample_vault_index, min_overlap=2, min_jaccard=0.3)

        # mental-health appears with multiple tags
        tag_pairs = {(c.tag_a, c.tag_b) for c in coocs}
        found_mental_health_pair = any("mental-health" in pair for pair in tag_pairs)
        assert found_mental_health_pair

    def test_respects_min_overlap(self, sample_vault_index: VaultIndex) -> None:
        """Higher min_overlap should return fewer results."""
        coocs_low = analyze_cooccurrence(sample_vault_index, min_overlap=1, min_jaccard=0.1)
        coocs_high = analyze_cooccurrence(sample_vault_index, min_overlap=3, min_jaccard=0.1)

        assert len(coocs_low) >= len(coocs_high)

    def test_jaccard_calculation(self, sample_vault_index: VaultIndex) -> None:
        """Jaccard similarity should be calculated correctly."""
        coocs = analyze_cooccurrence(sample_vault_index, min_overlap=1, min_jaccard=0.0)

        for cooc in coocs:
            # Jaccard should be between 0 and 1
            assert 0 <= cooc.jaccard_similarity <= 1


# =============================================================================
# Unit Tests: Orphan Tags
# =============================================================================


class TestFindOrphanTags:
    """Tests for find_orphan_tags function."""

    def test_finds_orphan_tags(self, sample_vault_index: VaultIndex) -> None:
        """Should find tags used only once."""
        orphans = find_orphan_tags(sample_vault_index)
        orphan_names = {o.tag for o in orphans}

        assert "unique-orphan-tag" in orphan_names
        assert "research" in orphan_names  # Only in note4

    def test_excludes_common_tags(self, sample_vault_index: VaultIndex) -> None:
        """Should not include tags used multiple times."""
        orphans = find_orphan_tags(sample_vault_index)
        orphan_names = {o.tag for o in orphans}

        assert "mental-health" not in orphan_names  # Used 4 times
        assert "autism" not in orphan_names  # Used 2 times

    def test_orphan_has_note_path(self, sample_vault_index: VaultIndex) -> None:
        """Each orphan should have a note_path."""
        orphans = find_orphan_tags(sample_vault_index)
        for orphan in orphans:
            assert orphan.note_path is not None


# =============================================================================
# Unit Tests: Plan Generation
# =============================================================================


class TestGeneratePlan:
    """Tests for generate_plan function."""

    def test_generates_complete_plan(self, sample_vault_index: VaultIndex) -> None:
        """Should generate a plan with all analysis types."""
        plan = generate_plan(sample_vault_index)

        assert isinstance(plan, TagHygienePlan)
        assert plan.analyzed_at is not None

    def test_plan_serialization(self, sample_vault_index: VaultIndex) -> None:
        """Plan should serialize to and from JSON."""
        plan = generate_plan(sample_vault_index)

        json_str = plan.to_json()
        assert isinstance(json_str, str)

        # Should be valid JSON
        parsed = json.loads(json_str)
        assert "consolidations" in parsed
        assert "orphan_tags" in parsed

    def test_plan_deserialization(self, sample_vault_index: VaultIndex, tmp_path: Path) -> None:
        """Plan should load from file correctly."""
        plan = generate_plan(sample_vault_index)

        # Save to file
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(plan.to_json(), encoding="utf-8")

        # Load back
        loaded = TagHygienePlan.from_file(plan_file)

        assert len(loaded.orphan_tags) == len(plan.orphan_tags)


# =============================================================================
# Unit Tests: Apply Consolidation
# =============================================================================


class TestApplyConsolidation:
    """Tests for apply_consolidation function."""

    def test_merge_tags(self, sample_note_file: Path) -> None:
        """Should merge tags in frontmatter."""
        import frontmatter

        consolidation = TagConsolidation(
            action="merge",
            from_tags=["neurodivergence"],
            to_tag="neurodivergent",
            affected_notes=[sample_note_file],
            note_count=1,
            apply=True,
        )

        result = apply_consolidation(sample_note_file, consolidation, create_backup_file=False)

        assert result is True

        # Check the file was updated
        post = frontmatter.load(sample_note_file)
        tags = post.metadata.get("tags", [])

        assert "neurodivergence" not in tags
        assert "neurodivergent" in tags

    def test_remove_tags(self, sample_note_file: Path) -> None:
        """Should remove tags for remove action."""
        import frontmatter

        consolidation = TagConsolidation(
            action="remove",
            from_tags=["neurodivergence"],
            to_tag=None,
            affected_notes=[sample_note_file],
            note_count=1,
            apply=True,
        )

        result = apply_consolidation(sample_note_file, consolidation, create_backup_file=False)

        assert result is True

        post = frontmatter.load(sample_note_file)
        tags = post.metadata.get("tags", [])

        assert "neurodivergence" not in tags

    def test_creates_backup(self, sample_note_file: Path) -> None:
        """Should create backup before modifying."""
        consolidation = TagConsolidation(
            action="merge",
            from_tags=["neurodivergence"],
            to_tag="neurodivergent",
            affected_notes=[sample_note_file],
            note_count=1,
            apply=True,
        )

        apply_consolidation(sample_note_file, consolidation, create_backup_file=True)

        backup_path = sample_note_file.with_suffix(".md.backup")
        assert backup_path.exists()

    def test_no_change_if_tag_missing(self, sample_note_file: Path) -> None:
        """Should return False if tag not in note."""
        consolidation = TagConsolidation(
            action="merge",
            from_tags=["nonexistent-tag"],
            to_tag="something",
            affected_notes=[sample_note_file],
            note_count=1,
            apply=True,
        )

        result = apply_consolidation(sample_note_file, consolidation, create_backup_file=False)

        assert result is False


# =============================================================================
# Unit Tests: Apply Plan
# =============================================================================


class TestApplyPlan:
    """Tests for apply_plan function."""

    def test_applies_all_consolidations(self, sample_note_file: Path, tmp_path: Path) -> None:
        """Should apply all consolidations marked with apply=True."""
        plan = TagHygienePlan(
            consolidations=[
                TagConsolidation(
                    action="merge",
                    from_tags=["neurodivergence"],
                    to_tag="neurodivergent",
                    affected_notes=[sample_note_file],
                    note_count=1,
                    apply=True,
                ),
            ],
            similar_tags=[],
            high_cooccurrence=[],
            orphan_tags=[],
        )

        modified, skipped = apply_plan(plan, create_backups=False)

        assert modified == 1
        assert skipped == 0

    def test_skips_apply_false(self, sample_note_file: Path) -> None:
        """Should skip consolidations with apply=False."""
        plan = TagHygienePlan(
            consolidations=[
                TagConsolidation(
                    action="merge",
                    from_tags=["neurodivergence"],
                    to_tag="neurodivergent",
                    affected_notes=[sample_note_file],
                    note_count=1,
                    apply=False,  # Should be skipped
                ),
            ],
            similar_tags=[],
            high_cooccurrence=[],
            orphan_tags=[],
        )

        modified, skipped = apply_plan(plan, create_backups=False)

        assert modified == 0
        assert skipped == 1  # Skipped because apply=False


# =============================================================================
# Unit Tests: Backup Creation
# =============================================================================


class TestCreateBackup:
    """Tests for create_backup function."""

    def test_creates_backup_file(self, sample_note_file: Path) -> None:
        """Should create a .backup file."""
        backup_path = create_backup(sample_note_file)

        assert backup_path.exists()
        assert backup_path.suffix == ".backup"
        assert backup_path.read_text() == sample_note_file.read_text()


# =============================================================================
# Model Tests
# =============================================================================


class TestModels:
    """Tests for Pydantic models."""

    def test_similar_tag_group_creation(self) -> None:
        """SimilarTagGroup should be created correctly."""
        group = SimilarTagGroup(
            canonical="neurodivergent",
            variants=["neurodivergence", "neurodiversity"],
            total_notes=13,
            similarity_scores={"neurodivergence": 0.92, "neurodiversity": 0.85},
        )

        assert group.canonical == "neurodivergent"
        assert len(group.variants) == 2

    def test_tag_cooccurrence_creation(self) -> None:
        """TagCooccurrence should be created correctly."""
        cooc = TagCooccurrence(
            tag_a="autism",
            tag_b="mental-health",
            co_occurrence_count=10,
            tag_a_total=14,
            tag_b_total=12,
            jaccard_similarity=0.625,
        )

        assert cooc.co_occurrence_count == 10

    def test_consolidation_creation(self) -> None:
        """TagConsolidation should be created correctly."""
        consolidation = TagConsolidation(
            action="merge",
            from_tags=["a", "b"],
            to_tag="c",
            affected_notes=[],
            note_count=5,
            apply=True,
        )

        assert consolidation.action == "merge"
        assert consolidation.apply is True
