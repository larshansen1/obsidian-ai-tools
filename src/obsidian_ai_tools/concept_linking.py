"""Concept linking for discovering connections between notes.

Uses TF-IDF similarity and keyword analysis to find related notes
and suggest wikilink insertions.
"""

import logging
import re
from pathlib import Path

from pydantic import BaseModel, Field
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .indexer import VaultIndex

logger = logging.getLogger(__name__)


# =============================================================================
# Models
# =============================================================================


class ConnectionSuggestion(BaseModel):
    """A suggested connection between notes."""

    source_note: Path = Field(..., description="Source note path")
    target_note: Path = Field(..., description="Target note path")
    target_title: str = Field(..., description="Target note title")
    similarity_score: float = Field(..., description="Similarity score (0-1)")
    connection_type: str = Field(..., description="Type: tfidf, keyword, backlink")
    keywords_shared: list[str] = Field(
        default_factory=list, description="Shared keywords"
    )


class OrphanNote(BaseModel):
    """A note with no incoming or outgoing links."""

    file_path: Path = Field(..., description="Path to orphan note")
    title: str = Field(..., description="Note title")
    has_outgoing: bool = Field(False, description="Has outgoing links")
    has_incoming: bool = Field(False, description="Has incoming links")


# =============================================================================
# Wikilink Extraction
# =============================================================================

# Regex to match [[wikilinks]] including optional |alias
WIKILINK_PATTERN = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def extract_wikilinks(content: str) -> set[str]:
    """Extract wikilink targets from note content.

    Args:
        content: Markdown content

    Returns:
        Set of linked note names (without [[]] brackets)
    """
    matches = WIKILINK_PATTERN.findall(content)
    return set(matches)


def normalize_title_for_link(title: str) -> str:
    """Normalize a title for comparison with wikilinks.

    Args:
        title: Note title to normalize

    Returns:
        Normalized title (lowercase, stripped)
    """
    return title.strip().lower()


def sanitize_for_wikilink(title: str) -> str:
    """Sanitize a title for use in a wikilink.

    Removes characters that are invalid in Obsidian wikilinks.

    Args:
        title: Title to sanitize

    Returns:
        Sanitized title safe for [[wikilinks]]
    """
    # Characters not allowed in wikilinks: / \ : | # ^ [ ]
    invalid_chars = r'/\:|#^[]'
    result = title
    for char in invalid_chars:
        result = result.replace(char, " ")
    # Collapse multiple spaces
    result = " ".join(result.split())
    return result.strip()


# =============================================================================
# Concept Linker
# =============================================================================


class ConceptLinker:
    """Discovers connections between notes using TF-IDF similarity."""

    def __init__(self, vault_index: VaultIndex):
        """Initialize linker with vault index.

        Args:
            vault_index: VaultIndex containing all notes
        """
        self.vault_index = vault_index
        self._tfidf_matrix: sparse.csr_matrix | None = None
        self._vectorizer: TfidfVectorizer | None = None
        self._note_paths: list[Path] = []

    def build_tfidf_index(self) -> None:
        """Build TF-IDF matrix from note contents.

        Creates a TF-IDF matrix for similarity calculations.
        """
        if not self.vault_index.notes:
            logger.warning("No notes in vault index")
            return

        # Prepare documents
        documents = []
        self._note_paths = []

        for note in self.vault_index.notes:
            # Combine title and content for better matching
            text = f"{note.title} {note.content}"
            documents.append(text)
            self._note_paths.append(note.file_path)

        # Build TF-IDF matrix
        self._vectorizer = TfidfVectorizer(
            max_features=5000,
            stop_words="english",
            ngram_range=(1, 2),  # Unigrams and bigrams
            min_df=1,
            max_df=0.95,
        )

        self._tfidf_matrix = self._vectorizer.fit_transform(documents)
        logger.info(f"Built TF-IDF matrix: {self._tfidf_matrix.shape}")

    def find_similar(
        self,
        note_path: Path,
        top_n: int = 5,
        threshold: float = 0.3,
    ) -> list[ConnectionSuggestion]:
        """Find notes similar to the given note.

        Args:
            note_path: Path to the source note
            top_n: Maximum number of suggestions
            threshold: Minimum similarity score (0-1)

        Returns:
            List of connection suggestions sorted by similarity
        """
        if self._tfidf_matrix is None:
            self.build_tfidf_index()

        if self._tfidf_matrix is None:
            return []

        # Find index of source note
        try:
            source_idx = self._note_paths.index(note_path)
        except ValueError:
            logger.warning(f"Note not found in index: {note_path}")
            return []

        # Calculate cosine similarity
        source_vector = self._tfidf_matrix[source_idx]
        similarities = cosine_similarity(source_vector, self._tfidf_matrix).flatten()

        # Get source note for extracting existing links
        source_note = next(
            (n for n in self.vault_index.notes if n.file_path == note_path), None
        )
        existing_links = set()
        if source_note:
            existing_links = {
                normalize_title_for_link(link)
                for link in extract_wikilinks(source_note.content)
            }

        # Build suggestions
        suggestions = []

        for idx, score in enumerate(similarities):
            # Skip self
            if idx == source_idx:
                continue

            # Skip below threshold
            if score < threshold:
                continue

            target_path = self._note_paths[idx]
            target_note = next(
                (n for n in self.vault_index.notes if n.file_path == target_path),
                None,
            )

            if not target_note:
                continue

            # Skip if already linked
            if normalize_title_for_link(target_note.title) in existing_links:
                continue

            # Extract shared keywords
            shared_keywords = self._extract_shared_keywords(source_idx, idx)

            suggestions.append(
                ConnectionSuggestion(
                    source_note=note_path,
                    target_note=target_path,
                    target_title=target_note.title,
                    similarity_score=round(score, 3),
                    connection_type="tfidf",
                    keywords_shared=shared_keywords[:5],
                )
            )

        # Sort by similarity and limit
        suggestions.sort(key=lambda x: x.similarity_score, reverse=True)
        return suggestions[:top_n]

    def find_all_connections(
        self,
        threshold: float = 0.3,
    ) -> list[ConnectionSuggestion]:
        """Find all pairwise connections in the indexed notes.

        Discovers all pairs of notes with similarity above threshold.
        Useful for scanning an entire folder.

        Args:
            threshold: Minimum similarity score (0-1)

        Returns:
            List of all connection suggestions, sorted by similarity
        """
        if self._tfidf_matrix is None:
            self.build_tfidf_index()

        if self._tfidf_matrix is None:
            return []

        # Calculate pairwise similarities
        from sklearn.metrics.pairwise import cosine_similarity as pairwise_cosine

        similarity_matrix = pairwise_cosine(self._tfidf_matrix)

        # Build a map of existing links for each note
        existing_links_map: dict[int, set[str]] = {}
        for idx, note in enumerate(self.vault_index.notes):
            existing_links_map[idx] = {
                normalize_title_for_link(link)
                for link in extract_wikilinks(note.content)
            }

        suggestions = []
        seen_pairs: set[tuple[int, int]] = set()

        for i in range(len(self._note_paths)):
            for j in range(len(self._note_paths)):
                # Skip self and already processed pairs
                if i >= j:
                    continue

                pair = (min(i, j), max(i, j))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                score = similarity_matrix[i, j]
                if score < threshold:
                    continue

                source_note = self.vault_index.notes[i]
                target_note = self.vault_index.notes[j]

                # Skip if already linked (either direction)
                if normalize_title_for_link(target_note.title) in existing_links_map[i]:
                    continue
                if normalize_title_for_link(source_note.title) in existing_links_map[j]:
                    continue

                shared_keywords = self._extract_shared_keywords(i, j)

                suggestions.append(
                    ConnectionSuggestion(
                        source_note=source_note.file_path,
                        target_note=target_note.file_path,
                        target_title=target_note.title,
                        similarity_score=round(score, 3),
                        connection_type="tfidf",
                        keywords_shared=shared_keywords[:5],
                    )
                )

        # Sort by similarity descending
        suggestions.sort(key=lambda x: x.similarity_score, reverse=True)
        return suggestions

    def _extract_shared_keywords(
        self, source_idx: int, target_idx: int, top_n: int = 5
    ) -> list[str]:
        """Extract shared keywords between two notes.

        Args:
            source_idx: Index of source note
            target_idx: Index of target note
            top_n: Number of keywords to return

        Returns:
            List of shared keywords
        """
        if self._vectorizer is None or self._tfidf_matrix is None:
            return []

        feature_names = self._vectorizer.get_feature_names_out()

        # Get non-zero features for both notes
        source_features = set(self._tfidf_matrix[source_idx].nonzero()[1])
        target_features = set(self._tfidf_matrix[target_idx].nonzero()[1])

        # Find shared features
        shared = source_features & target_features

        # Get feature names and sort by combined TF-IDF weight
        keywords_with_weight = []
        for feature_idx in shared:
            weight = (
                self._tfidf_matrix[source_idx, feature_idx]
                + self._tfidf_matrix[target_idx, feature_idx]
            )
            keywords_with_weight.append((feature_names[feature_idx], weight))

        keywords_with_weight.sort(key=lambda x: x[1], reverse=True)
        return [kw for kw, _ in keywords_with_weight[:top_n]]

    def find_orphans(self) -> list[OrphanNote]:
        """Find notes with no incoming or outgoing links.

        Returns:
            List of orphan notes
        """
        orphans = []

        # Build a map of all note titles for incoming link detection
        title_to_path: dict[str, Path] = {}
        for note in self.vault_index.notes:
            normalized = normalize_title_for_link(note.title)
            title_to_path[normalized] = note.file_path
            # Also add filename without extension
            stem = normalize_title_for_link(note.file_path.stem)
            title_to_path[stem] = note.file_path

        # Track incoming links
        incoming_links: dict[Path, set[Path]] = {
            note.file_path: set() for note in self.vault_index.notes
        }

        # Scan all notes for outgoing links
        for note in self.vault_index.notes:
            outgoing = extract_wikilinks(note.content)

            for link in outgoing:
                normalized_link = normalize_title_for_link(link)
                if normalized_link in title_to_path:
                    target_path = title_to_path[normalized_link]
                    incoming_links[target_path].add(note.file_path)

        # Find orphans
        for note in self.vault_index.notes:
            outgoing = extract_wikilinks(note.content)
            has_outgoing = len(outgoing) > 0
            has_incoming = len(incoming_links.get(note.file_path, set())) > 0

            if not has_outgoing and not has_incoming:
                orphans.append(
                    OrphanNote(
                        file_path=note.file_path,
                        title=note.title,
                        has_outgoing=has_outgoing,
                        has_incoming=has_incoming,
                    )
                )

        return orphans

    def insert_wikilinks(
        self,
        note_path: Path,
        suggestions: list[ConnectionSuggestion],
        dry_run: bool = True,
    ) -> list[str]:
        """Insert wikilinks into a note.

        Appends a "Related Notes" section at the end of the note.

        Args:
            note_path: Path to note to modify
            suggestions: Suggestions to insert
            dry_run: If True, return changes without modifying file

        Returns:
            List of inserted link strings
        """
        if not suggestions:
            return []

        # Read current content
        try:
            content = note_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to read note: {e}")
            return []

        # Determine vault root from index path
        vault_root = self.vault_index.index_path.parent.parent

        # Build links section
        links_section = "\n\n## Related Notes\n\n"
        inserted_links = []

        for suggestion in suggestions:
            # Use relative path (without extension) as link target to ensure validity
            try:
                rel_path = suggestion.target_note.relative_to(vault_root)
                link_path = str(rel_path.with_suffix(""))
            except ValueError:
                # Fallback if path logic fails
                link_path = sanitize_for_wikilink(suggestion.target_title)

            # Use Title as alias
            link = f"[[{link_path}|{suggestion.target_title}]]"
            links_section += f"- {link} (similarity: {suggestion.similarity_score:.2f})\n"
            inserted_links.append(link)

        if dry_run:
            logger.info(f"Dry run: Would insert {len(inserted_links)} links")
            return inserted_links

        # Write updated content
        try:
            new_content = content.rstrip() + links_section
            note_path.write_text(new_content, encoding="utf-8")
            logger.info(f"Inserted {len(inserted_links)} links into {note_path}")
        except Exception as e:
            logger.error(f"Failed to write note: {e}")
            return []

        return inserted_links


# =============================================================================
# Convenience Functions
# =============================================================================


def find_connections(
    vault_index: VaultIndex,
    note_path: Path,
    top_n: int = 5,
    threshold: float = 0.3,
) -> list[ConnectionSuggestion]:
    """Find connections for a specific note.

    Args:
        vault_index: Vault index with all notes
        note_path: Path to note to find connections for
        top_n: Maximum suggestions
        threshold: Minimum similarity

    Returns:
        List of connection suggestions
    """
    linker = ConceptLinker(vault_index)
    return linker.find_similar(note_path, top_n, threshold)


def find_orphan_notes(vault_index: VaultIndex) -> list[OrphanNote]:
    """Find all orphan notes in vault.

    Args:
        vault_index: Vault index with all notes

    Returns:
        List of orphan notes
    """
    linker = ConceptLinker(vault_index)
    return linker.find_orphans()
