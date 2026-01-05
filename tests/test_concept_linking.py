"""Tests for concept linking functionality."""

from pathlib import Path

import pytest

from obsidian_ai_tools.concept_linking import (
    ConceptLinker,
    ConnectionSuggestion,
    OrphanNote,
    extract_wikilinks,
    find_connections,
    find_orphan_notes,
    normalize_title_for_link,
)
from obsidian_ai_tools.indexer import NoteMetadata, VaultIndex

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_notes() -> list[NoteMetadata]:
    """Create sample notes for testing."""
    return [
        NoteMetadata(
            file_path=Path("/vault/AI/Attention.md"),
            title="Attention Mechanisms",
            tags=["ai", "llm"],
            content="Attention mechanisms in neural networks allow models to focus "
            "on relevant parts of the input. Self-attention is key to transformers.",
            modified_time=1000.0,
        ),
        NoteMetadata(
            file_path=Path("/vault/AI/Transformers.md"),
            title="Transformers",
            tags=["ai", "llm"],
            content="Transformers use self-attention and feedforward layers. "
            "The attention mechanism enables parallel processing of sequences.",
            modified_time=1001.0,
        ),
        NoteMetadata(
            file_path=Path("/vault/ML/Backprop.md"),
            title="Backpropagation",
            tags=["ml", "neural-nets"],
            content="Backpropagation is an algorithm for training neural networks. "
            "It computes gradients through the chain rule.",
            modified_time=1002.0,
        ),
        NoteMetadata(
            file_path=Path("/vault/Reading/DeepLearning.md"),
            title="Deep Learning Book",
            tags=["reading"],
            content="Notes from the Deep Learning book by Goodfellow. "
            "Chapter on attention and transformers was interesting.",
            modified_time=1003.0,
        ),
    ]


@pytest.fixture
def vault_index(sample_notes: list[NoteMetadata], tmp_path: Path) -> VaultIndex:
    """Create a vault index with sample notes."""
    index_path = tmp_path / ".kai" / "vault_index.json"
    return VaultIndex(notes=sample_notes, index_path=index_path)


# =============================================================================
# Tests for Wikilink Extraction
# =============================================================================


class TestExtractWikilinks:
    """Tests for extract_wikilinks function."""

    def test_extract_simple_wikilinks(self) -> None:
        """Test extracting simple wikilinks."""
        content = "This is a note with [[Link 1]] and [[Link 2]]."
        links = extract_wikilinks(content)
        assert links == {"Link 1", "Link 2"}

    def test_extract_wikilinks_with_alias(self) -> None:
        """Test extracting wikilinks with aliases."""
        content = "See [[Actual Note|display text]] for more info."
        links = extract_wikilinks(content)
        assert links == {"Actual Note"}

    def test_extract_no_wikilinks(self) -> None:
        """Test content without wikilinks."""
        content = "This is plain text with no links."
        links = extract_wikilinks(content)
        assert links == set()

    def test_extract_wikilinks_with_paths(self) -> None:
        """Test wikilinks with folder paths."""
        content = "Check [[Folder/Subfolder/Note]] for details."
        links = extract_wikilinks(content)
        assert links == {"Folder/Subfolder/Note"}


class TestNormalizeTitleForLink:
    """Tests for normalize_title_for_link function."""

    def test_normalize_lowercase(self) -> None:
        """Test lowercase normalization."""
        assert normalize_title_for_link("My Note") == "my note"

    def test_normalize_strips_whitespace(self) -> None:
        """Test whitespace stripping."""
        assert normalize_title_for_link("  Note  ") == "note"


# =============================================================================
# Tests for ConceptLinker
# =============================================================================


class TestConceptLinker:
    """Tests for ConceptLinker class."""

    def test_build_tfidf_index(self, vault_index: VaultIndex) -> None:
        """Test building TF-IDF index."""
        linker = ConceptLinker(vault_index)
        linker.build_tfidf_index()

        assert linker._tfidf_matrix is not None
        assert linker._tfidf_matrix.shape[0] == len(vault_index.notes)

    def test_find_similar_returns_results(self, vault_index: VaultIndex) -> None:
        """Test finding similar notes."""
        linker = ConceptLinker(vault_index)
        linker.build_tfidf_index()

        attention_note = Path("/vault/AI/Attention.md")
        suggestions = linker.find_similar(attention_note, top_n=3, threshold=0.1)

        assert len(suggestions) > 0
        assert all(isinstance(s, ConnectionSuggestion) for s in suggestions)
        # Transformers should be most similar to Attention
        assert suggestions[0].target_title == "Transformers"

    def test_find_similar_respects_threshold(self, vault_index: VaultIndex) -> None:
        """Test that threshold filters results."""
        linker = ConceptLinker(vault_index)
        linker.build_tfidf_index()

        attention_note = Path("/vault/AI/Attention.md")

        # With very high threshold
        high_threshold = linker.find_similar(attention_note, threshold=0.99)
        # With low threshold
        low_threshold = linker.find_similar(attention_note, threshold=0.01)

        assert len(high_threshold) <= len(low_threshold)

    def test_find_similar_skips_self(self, vault_index: VaultIndex) -> None:
        """Test that a note is not suggested to itself."""
        linker = ConceptLinker(vault_index)
        linker.build_tfidf_index()

        attention_note = Path("/vault/AI/Attention.md")
        suggestions = linker.find_similar(attention_note, threshold=0)

        source_paths = [s.target_note for s in suggestions]
        assert attention_note not in source_paths

    def test_find_similar_note_not_found(self, vault_index: VaultIndex) -> None:
        """Test handling of non-existent note."""
        linker = ConceptLinker(vault_index)
        linker.build_tfidf_index()

        suggestions = linker.find_similar(Path("/vault/NonExistent.md"))
        assert suggestions == []

    def test_extract_shared_keywords(self, vault_index: VaultIndex) -> None:
        """Test extracting shared keywords."""
        linker = ConceptLinker(vault_index)
        linker.build_tfidf_index()

        # Attention (idx 0) and Transformers (idx 1) should share keywords
        keywords = linker._extract_shared_keywords(0, 1)
        assert len(keywords) > 0
        assert "attention" in [k.lower() for k in keywords]


class TestFindOrphans:
    """Tests for orphan note detection."""

    def test_find_orphans_all_unlinked(
        self, sample_notes: list[NoteMetadata], tmp_path: Path
    ) -> None:
        """Test finding orphans when all notes are unlinked."""
        vault_index = VaultIndex(
            notes=sample_notes, index_path=tmp_path / "index.json"
        )
        linker = ConceptLinker(vault_index)
        orphans = linker.find_orphans()

        # All notes have no links, so all should be orphans
        assert len(orphans) == len(sample_notes)

    def test_find_orphans_with_links(self, tmp_path: Path) -> None:
        """Test finding orphans when some notes have links."""
        notes = [
            NoteMetadata(
                file_path=Path("/vault/Note1.md"),
                title="Note 1",
                tags=[],
                content="This links to [[Note 2]]",
                modified_time=1000.0,
            ),
            NoteMetadata(
                file_path=Path("/vault/Note2.md"),
                title="Note 2",
                tags=[],
                content="This note has no links",
                modified_time=1001.0,
            ),
            NoteMetadata(
                file_path=Path("/vault/Orphan.md"),
                title="Orphan",
                tags=[],
                content="This note has no links",
                modified_time=1002.0,
            ),
        ]
        vault_index = VaultIndex(notes=notes, index_path=tmp_path / "index.json")
        linker = ConceptLinker(vault_index)
        orphans = linker.find_orphans()

        # Note 1 has outgoing, Note 2 has incoming, only Orphan is truly orphan
        assert len(orphans) == 1
        assert orphans[0].title == "Orphan"


class TestInsertWikilinks:
    """Tests for wikilink insertion."""

    def test_insert_wikilinks_dry_run(
        self, vault_index: VaultIndex, tmp_path: Path
    ) -> None:
        """Test dry run doesn't modify file."""
        # Setup vault root properly from vault_index
        vault_root = vault_index.index_path.parent.parent
        note_path = tmp_path / "test.md"
        note_path.write_text("# Test Note\n\nSome content.")

        suggestions = [
            ConnectionSuggestion(
                source_note=note_path,
                target_note=vault_root / "Target.md",
                target_title="Target Note",
                similarity_score=0.8,
                connection_type="tfidf",
                keywords_shared=["keyword"],
            )
        ]

        linker = ConceptLinker(vault_index)
        links = linker.insert_wikilinks(note_path, suggestions, dry_run=True)

        assert len(links) == 1
        # Expect aliased link: [[Target|Target Note]]
        assert "[[Target|Target Note]]" in links
        # Content should be unchanged
        assert "Related Notes" not in note_path.read_text()

    def test_insert_wikilinks_modifies_file(
        self, vault_index: VaultIndex, tmp_path: Path
    ) -> None:
        """Test actual insertion modifies file."""
        vault_root = vault_index.index_path.parent.parent
        note_path = tmp_path / "test.md"
        note_path.write_text("# Test Note\n\nSome content.")

        suggestions = [
            ConnectionSuggestion(
                source_note=note_path,
                target_note=vault_root / "Target.md",
                target_title="Target Note",
                similarity_score=0.8,
                connection_type="tfidf",
                keywords_shared=["keyword"],
            )
        ]

        linker = ConceptLinker(vault_index)
        links = linker.insert_wikilinks(note_path, suggestions, dry_run=False)

        assert len(links) == 1
        content = note_path.read_text()
        assert "## Related Notes" in content
        assert "[[Target|Target Note]]" in content


# =============================================================================
# Tests for Convenience Functions
# =============================================================================


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_find_connections(self, vault_index: VaultIndex) -> None:
        """Test find_connections function."""
        attention_note = Path("/vault/AI/Attention.md")
        suggestions = find_connections(vault_index, attention_note, top_n=2)

        assert len(suggestions) <= 2
        assert all(isinstance(s, ConnectionSuggestion) for s in suggestions)

    def test_find_orphan_notes(self, vault_index: VaultIndex) -> None:
        """Test find_orphan_notes function."""
        orphans = find_orphan_notes(vault_index)

        assert all(isinstance(o, OrphanNote) for o in orphans)


# =============================================================================
# CLI Integration Tests
# =============================================================================


class TestConnectCommand:
    """Integration tests for kai connect CLI command."""

    def test_connect_no_args(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test connect fails without --note or --orphans."""
        from typer.testing import CliRunner

        from obsidian_ai_tools.cli import app

        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("LLM_MODEL", "test-model")

        runner = CliRunner()
        result = runner.invoke(app, ["connect"])

        assert result.exit_code == 1
        assert "specify --note" in result.output or "specify --folder" in result.output

    def test_connect_note_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test connect with non-existent note."""
        from typer.testing import CliRunner

        from obsidian_ai_tools.cli import app

        monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(tmp_path))
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setenv("LLM_MODEL", "test-model")

        runner = CliRunner()
        result = runner.invoke(app, ["connect", "--note", "nonexistent.md"])

        assert result.exit_code == 1
        assert "not found" in result.output
