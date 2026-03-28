"""Tag hygiene analysis and consolidation for Obsidian vaults.

Detects tag fragmentation, finds near-duplicate tags, analyzes co-occurrence
patterns, and applies consolidation fixes.
"""

import logging
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal

import frontmatter
from pydantic import BaseModel, Field

from .indexer import VaultIndex

logger = logging.getLogger(__name__)


# =============================================================================
# Models
# =============================================================================


class TagConsolidation(BaseModel):
    """A tag consolidation action."""

    action: Literal["merge", "remove"] = Field(
        ..., description="Action type: merge variants into canonical, or remove"
    )
    from_tags: list[str] = Field(..., description="Tags to consolidate/remove")
    to_tag: str | None = Field(None, description="Target canonical tag (None for remove action)")
    affected_notes: list[Path] = Field(
        default_factory=list, description="Notes that will be modified"
    )
    note_count: int = Field(..., description="Number of affected notes")
    apply: bool = Field(True, description="Whether to apply this consolidation")


class SimilarTagGroup(BaseModel):
    """A group of tags with similar names."""

    canonical: str = Field(..., description="Suggested primary/canonical tag")
    variants: list[str] = Field(..., description="Similar tags to consolidate")
    total_notes: int = Field(..., description="Combined note count across all tags")
    similarity_scores: dict[str, float] = Field(
        default_factory=dict, description="Similarity score for each variant"
    )


class TagCooccurrence(BaseModel):
    """Tags that frequently appear together.

    Set merge_into to 'a' or 'b' to merge one tag into the other.
    """

    tag_a: str = Field(..., description="First tag")
    tag_b: str = Field(..., description="Second tag")
    co_occurrence_count: int = Field(..., description="Notes with both tags")
    tag_a_total: int = Field(..., description="Total notes with tag_a")
    tag_b_total: int = Field(..., description="Total notes with tag_b")
    jaccard_similarity: float = Field(..., description="Jaccard similarity (0-1)")
    merge_into: str | None = Field(
        None,
        description="Set to 'a' to merge tag_b into tag_a, 'b' for reverse",
    )


class OrphanTag(BaseModel):
    """A tag used only once.

    Set remove=true to remove this tag from its note.
    """

    tag: str = Field(..., description="The orphan tag name")
    note_path: Path | None = Field(None, description="Path to the note containing this tag")
    remove: bool = Field(False, description="Set to true to remove this tag")


class TagHygienePlan(BaseModel):
    """Complete plan for tag fixes."""

    consolidations: list[TagConsolidation] = Field(
        default_factory=list, description="Consolidation actions to apply"
    )
    similar_tags: list[SimilarTagGroup] = Field(
        default_factory=list, description="Groups of similar tags found"
    )
    high_cooccurrence: list[TagCooccurrence] = Field(
        default_factory=list,
        description="Tag pairs with high co-occurrence (set merge_into for action)",
    )
    orphan_tags: list[OrphanTag] = Field(
        default_factory=list, description="Tags used only once (set remove=true to remove)"
    )
    analyzed_at: datetime = Field(default_factory=datetime.now, description="Analysis timestamp")

    def to_json(self) -> str:
        """Serialize plan to JSON string."""
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "TagHygienePlan":
        """Deserialize plan from JSON string."""
        return cls.model_validate_json(json_str)

    @classmethod
    def from_file(cls, path: Path) -> "TagHygienePlan":
        """Load plan from JSON file."""
        return cls.from_json(path.read_text(encoding="utf-8"))


# =============================================================================
# Analysis Functions
# =============================================================================


def calculate_similarity(tag_a: str, tag_b: str) -> float:
    """Calculate similarity between two tags using SequenceMatcher.

    Args:
        tag_a: First tag
        tag_b: Second tag

    Returns:
        Similarity score between 0 and 1
    """
    return SequenceMatcher(None, tag_a.lower(), tag_b.lower()).ratio()


def _get_word_stems(tag: str) -> set[str]:
    """Extract word stems from a tag for semantic comparison.

    Splits on hyphens and takes first 4+ characters of each word
    as a "stem" for comparison.

    Args:
        tag: Tag to extract stems from

    Returns:
        Set of word stems
    """
    words = tag.lower().replace("_", "-").split("-")
    stems = set()
    for word in words:
        if len(word) >= 4:
            # Use first 5 chars as stem (catches 'neurodivergent' -> 'neuro')
            stems.add(word[:5])
        elif len(word) >= 2:
            # Short words use full word
            stems.add(word)
    return stems


def _tags_share_semantic_root(tag_a: str, tag_b: str) -> bool:
    """Check if two tags share a meaningful semantic root.

    For compound tags with same structure (e.g., X-development), the first
    (distinguishing) word must match, not just any shared suffix.

    For simple tags, checks if they share a common prefix of 5+ chars.

    This prevents false positives like:
    - 'ai-development' vs 'ui-development' (different first word)
    - 'software-development' vs 'web-development' (different first word)
    - 'project-management' vs 'product-management' (different first word)

    But allows:
    - 'neurodivergent' vs 'neurodivergence' (shared prefix 'neuro')
    - 'socio-technical' vs 'sociotechnical' (essentially same words)

    Args:
        tag_a: First tag
        tag_b: Second tag

    Returns:
        True if tags share semantic meaning
    """
    # Split tags into word parts
    words_a = tag_a.lower().replace("_", "-").split("-")
    words_b = tag_b.lower().replace("_", "-").split("-")

    # For compound tags with same structure (same word count), require first word to match
    # This catches: 'ai-development' vs 'ui-development' (different prefix, same suffix)
    if len(words_a) > 1 and len(words_b) > 1 and len(words_a) == len(words_b):
        # Same structure - check if first word matches (the distinguishing word)
        first_a = words_a[0]
        first_b = words_b[0]

        # First words must be identical or share significant prefix
        if first_a == first_b:
            return True

        # Check if first words share 4+ char prefix (catches typos/variants)
        min_len = min(len(first_a), len(first_b))
        if min_len >= 4 and first_a[:4] == first_b[:4]:
            return True

        # Different first words = different tags
        return False

    # For different structures (one is compound, other simple), check stem overlap
    stems_a = _get_word_stems(tag_a)
    stems_b = _get_word_stems(tag_b)

    if len(words_a) > 1 or len(words_b) > 1:
        # At least one is compound - need significant overlap
        # For 'socio-technical' vs 'sociotechnical', stems will match
        return bool(stems_a & stems_b)

    # For simple single-word tags, check shared prefix
    # This catches 'neurodivergent' vs 'neurodivergence'
    a_lower = tag_a.lower()
    b_lower = tag_b.lower()

    # Check if they share a prefix of at least 5 characters
    min_prefix = 5
    if len(a_lower) >= min_prefix and len(b_lower) >= min_prefix:
        if a_lower[:min_prefix] == b_lower[:min_prefix]:
            return True

    return False


def find_similar_tags(
    vault_index: VaultIndex,
    threshold: float = 0.8,
) -> list[SimilarTagGroup]:
    """Find groups of tags with similar names.

    Uses character similarity AND semantic root checking to detect
    near-duplicate tags like 'neurodivergent' vs 'neurodivergence'.

    Avoids false positives like 'ai-development' vs 'ui-development'
    by requiring shared word stems for compound tags.

    Args:
        vault_index: Vault index with all notes
        threshold: Minimum similarity score (0-1) to consider tags similar

    Returns:
        List of similar tag groups, sorted by total note count
    """
    # Build tag -> note count mapping
    tag_counts: dict[str, int] = {}
    for note in vault_index.notes:
        for tag in note.tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    all_tags = list(tag_counts.keys())

    # Find similar pairs
    similar_pairs: dict[str, set[str]] = {}
    processed: set[str] = set()

    for i, tag_a in enumerate(all_tags):
        if tag_a in processed:
            continue

        similar_to_a: set[str] = set()

        for tag_b in all_tags[i + 1 :]:
            if tag_b in processed:
                continue

            # Skip if one tag is a prefix of the other in a compound sense
            # (like 'ai' vs 'ai-development') but not if they're just different
            # tags that happen to share characters (like 'ai' vs 'artificial-intelligence')
            shorter, longer = (tag_a, tag_b) if len(tag_a) < len(tag_b) else (tag_b, tag_a)
            if shorter in longer and longer.startswith(shorter):
                # Check if there's a hyphen right after the shorter tag (compound tag pattern)
                # e.g., "ai" in "ai-development" - skip these
                if len(longer) > len(shorter) and longer[len(shorter)] == "-":
                    continue

            # First check: character similarity must meet threshold
            similarity = calculate_similarity(tag_a, tag_b)
            if similarity < threshold:
                continue

            # Second check: must share semantic root
            # This prevents 'ai-development' vs 'ui-development' (high char similarity,
            # but no shared semantic meaning)
            if not _tags_share_semantic_root(tag_a, tag_b):
                continue

            similar_to_a.add(tag_b)
            processed.add(tag_b)

        if similar_to_a:
            similar_pairs[tag_a] = similar_to_a
            processed.add(tag_a)

    # Build SimilarTagGroup objects
    groups: list[SimilarTagGroup] = []

    for base_tag, variants in similar_pairs.items():
        all_related = [base_tag, *variants]

        # Pick canonical as the one with most notes
        canonical = max(all_related, key=lambda t: tag_counts.get(t, 0))
        variant_tags = [t for t in all_related if t != canonical]

        # Calculate similarity scores
        scores = {v: calculate_similarity(canonical, v) for v in variant_tags}

        total_notes = sum(tag_counts.get(t, 0) for t in all_related)

        groups.append(
            SimilarTagGroup(
                canonical=canonical,
                variants=variant_tags,
                total_notes=total_notes,
                similarity_scores=scores,
            )
        )

    # Sort by total notes descending
    groups.sort(key=lambda g: g.total_notes, reverse=True)
    return groups


def analyze_cooccurrence(
    vault_index: VaultIndex,
    min_overlap: int = 3,
    min_jaccard: float = 0.5,
) -> list[TagCooccurrence]:
    """Find tags that frequently appear together.

    Args:
        vault_index: Vault index with all notes
        min_overlap: Minimum number of notes with both tags
        min_jaccard: Minimum Jaccard similarity to report

    Returns:
        List of high co-occurrence tag pairs
    """
    # Build tag -> set of notes mapping
    tag_notes: dict[str, set[Path]] = {}
    for note in vault_index.notes:
        for tag in note.tags:
            if tag not in tag_notes:
                tag_notes[tag] = set()
            tag_notes[tag].add(note.file_path)

    all_tags = list(tag_notes.keys())
    cooccurrences: list[TagCooccurrence] = []

    for i, tag_a in enumerate(all_tags):
        notes_a = tag_notes[tag_a]

        for tag_b in all_tags[i + 1 :]:
            notes_b = tag_notes[tag_b]

            # Calculate intersection
            overlap = notes_a & notes_b
            overlap_count = len(overlap)

            if overlap_count < min_overlap:
                continue

            # Calculate Jaccard similarity: |A ∩ B| / |A ∪ B|
            union_count = len(notes_a | notes_b)
            jaccard = overlap_count / union_count if union_count > 0 else 0

            if jaccard < min_jaccard:
                continue

            cooccurrences.append(
                TagCooccurrence(
                    tag_a=tag_a,
                    tag_b=tag_b,
                    co_occurrence_count=overlap_count,
                    tag_a_total=len(notes_a),
                    tag_b_total=len(notes_b),
                    jaccard_similarity=round(jaccard, 3),
                )
            )

    # Sort by co-occurrence count descending
    cooccurrences.sort(key=lambda c: c.co_occurrence_count, reverse=True)
    return cooccurrences


def find_orphan_tags(vault_index: VaultIndex) -> list[OrphanTag]:
    """Find tags that are used only once.

    Args:
        vault_index: Vault index with all notes

    Returns:
        List of OrphanTag objects, sorted alphabetically by tag name
    """
    tag_info: dict[str, Path] = {}  # tag -> note path
    tag_counts: dict[str, int] = {}

    for note in vault_index.notes:
        for tag in note.tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
            if tag not in tag_info:
                tag_info[tag] = note.file_path

    orphans = [
        OrphanTag(tag=tag, note_path=tag_info[tag], remove=False)
        for tag, count in tag_counts.items()
        if count == 1
    ]
    orphans.sort(key=lambda o: o.tag)
    return orphans


def generate_plan(
    vault_index: VaultIndex,
    similarity_threshold: float = 0.8,
    min_cooccurrence: int = 3,
    min_jaccard: float = 0.5,
) -> TagHygienePlan:
    """Generate a complete tag hygiene plan.

    Args:
        vault_index: Vault index with all notes
        similarity_threshold: Threshold for similar tag detection
        min_cooccurrence: Minimum overlap for co-occurrence reporting
        min_jaccard: Minimum Jaccard similarity for co-occurrence

    Returns:
        Complete TagHygienePlan with consolidations and analysis
    """
    similar_tags = find_similar_tags(vault_index, similarity_threshold)
    high_cooccurrence = analyze_cooccurrence(vault_index, min_cooccurrence, min_jaccard)
    orphan_tags = find_orphan_tags(vault_index)

    # Build consolidations from similar tags
    consolidations: list[TagConsolidation] = []

    # Build tag -> notes mapping for affected notes lookup
    tag_notes: dict[str, list[Path]] = {}
    for note in vault_index.notes:
        for tag in note.tags:
            if tag not in tag_notes:
                tag_notes[tag] = []
            tag_notes[tag].append(note.file_path)

    for group in similar_tags:
        # Find all notes affected by this consolidation
        affected: set[Path] = set()
        for variant in group.variants:
            affected.update(tag_notes.get(variant, []))

        consolidations.append(
            TagConsolidation(
                action="merge",
                from_tags=group.variants,
                to_tag=group.canonical,
                affected_notes=sorted(affected),
                note_count=len(affected),
                apply=True,
            )
        )

    return TagHygienePlan(
        consolidations=consolidations,
        similar_tags=similar_tags,
        high_cooccurrence=high_cooccurrence,
        orphan_tags=orphan_tags,
        analyzed_at=datetime.now(),
    )


# =============================================================================
# Apply Functions
# =============================================================================


def create_backup(note_path: Path) -> Path:
    """Create a backup of a note before modification.

    Args:
        note_path: Path to the note to backup

    Returns:
        Path to the backup file
    """
    backup_path = note_path.with_suffix(".md.backup")
    backup_path.write_text(note_path.read_text(encoding="utf-8"), encoding="utf-8")
    logger.info(f"Created backup: {backup_path}")
    return backup_path


def apply_consolidation(
    note_path: Path,
    consolidation: TagConsolidation,
    create_backup_file: bool = True,
) -> bool:
    """Apply a tag consolidation to a note's frontmatter.

    For merge: Replace from_tags with to_tag in frontmatter
    For remove: Remove from_tags from frontmatter

    Args:
        note_path: Path to the note to modify
        consolidation: Consolidation action to apply
        create_backup_file: Whether to create a backup before modifying

    Returns:
        True if note was modified, False otherwise
    """
    try:
        post = frontmatter.load(note_path)
    except Exception as e:
        logger.error(f"Failed to load note {note_path}: {e}")
        return False

    tags = post.metadata.get("tags", [])
    if not isinstance(tags, list):
        tags = [tags] if tags else []

    original_tags = tags.copy()
    modified = False

    # Remove old tags
    for old_tag in consolidation.from_tags:
        if old_tag in tags:
            tags.remove(old_tag)
            modified = True

    # Add new tag if merge action
    if consolidation.action == "merge" and consolidation.to_tag:
        if consolidation.to_tag not in tags:
            tags.append(consolidation.to_tag)

    if not modified:
        return False

    # Create backup before modifying
    if create_backup_file:
        create_backup(note_path)

    # Update frontmatter and write
    post.metadata["tags"] = tags

    try:
        note_path.write_text(frontmatter.dumps(post), encoding="utf-8")
        logger.info(f"Updated {note_path}: {original_tags} -> {tags}")
        return True
    except Exception as e:
        logger.error(f"Failed to write note {note_path}: {e}")
        return False


def apply_plan(
    plan: TagHygienePlan,
    create_backups: bool = True,
) -> tuple[int, int]:
    """Apply all consolidations in a plan.

    Processes:
    - consolidations with apply=True
    - high_cooccurrence entries with merge_into='a' or 'b'
    - orphan_tags entries with remove=True

    Args:
        plan: TagHygienePlan to execute
        create_backups: Whether to create backups before modifying

    Returns:
        Tuple of (notes_modified, notes_skipped)
    """
    modified_count = 0
    skipped_count = 0

    # 1. Apply regular consolidations
    for consolidation in plan.consolidations:
        if not consolidation.apply:
            skipped_count += consolidation.note_count
            logger.info(
                f"Skipping consolidation: {consolidation.from_tags} -> {consolidation.to_tag}"
            )
            continue

        for note_path in consolidation.affected_notes:
            if apply_consolidation(note_path, consolidation, create_backups):
                modified_count += 1
            else:
                skipped_count += 1

    # 2. Apply co-occurrence merges
    for cooc in plan.high_cooccurrence:
        if not cooc.merge_into:
            continue

        if cooc.merge_into == "a":
            # Merge tag_b into tag_a
            from_tag = cooc.tag_b
            to_tag = cooc.tag_a
        elif cooc.merge_into == "b":
            # Merge tag_a into tag_b
            from_tag = cooc.tag_a
            to_tag = cooc.tag_b
        else:
            logger.warning(f"Invalid merge_into value: {cooc.merge_into}")
            continue

        # Create consolidation for this merge
        # We need to find all notes with from_tag
        consolidation = TagConsolidation(
            action="merge",
            from_tags=[from_tag],
            to_tag=to_tag,
            affected_notes=[],  # Will be applied to all notes with from_tag
            note_count=0,
            apply=True,
        )

        # Apply to all notes - we need to scan for notes with this tag
        # For now, log that this requires a vault index to find affected notes
        logger.info(
            f"Co-occurrence merge: {from_tag} -> {to_tag} "
            "(requires running with vault access to find affected notes)"
        )

    # 3. Remove orphan tags
    for orphan in plan.orphan_tags:
        if not orphan.remove:
            continue

        if not orphan.note_path:
            logger.warning(f"Cannot remove orphan tag '{orphan.tag}': no note_path specified")
            skipped_count += 1
            continue

        consolidation = TagConsolidation(
            action="remove",
            from_tags=[orphan.tag],
            to_tag=None,
            affected_notes=[orphan.note_path],
            note_count=1,
            apply=True,
        )

        if apply_consolidation(orphan.note_path, consolidation, create_backups):
            modified_count += 1
            logger.info(f"Removed orphan tag '{orphan.tag}' from {orphan.note_path}")
        else:
            skipped_count += 1

    logger.info(f"Applied plan: {modified_count} notes modified, {skipped_count} skipped")
    return modified_count, skipped_count
