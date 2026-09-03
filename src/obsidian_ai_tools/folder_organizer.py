"""Folder organization for inbox notes based on tag rules."""

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .indexer import parse_frontmatter


class FolderOrganizerError(Exception):
    """Base exception for folder organizer errors."""

    pass


class InvalidRulesError(FolderOrganizerError):
    """Raised when folder rules are invalid."""

    pass


class PathTraversalError(FolderOrganizerError):
    """Raised when path traversal attempt is detected."""

    pass


class NoteToMove(BaseModel):
    """A note that needs to be moved to a folder."""

    file_path: Path = Field(..., description="Path to note file")
    title: str = Field(..., description="Note title")
    tags: list[str] = Field(default_factory=list, description="Note tags")
    best_folder: str | None = Field(None, description="Target folder path")
    matched_tags: list[str] = Field(default_factory=list, description="Tags that matched the rule")
    score: float = Field(0.0, description="Confidence score for folder selection")

    # For backward compatibility with display code that expects matched_tag
    @property
    def matched_tag(self) -> str | None:
        """Return first matched tag for backward compatibility."""
        return self.matched_tags[0] if self.matched_tags else None


class RuleSuggestion(BaseModel):
    """A suggested folder rule for inbox notes without a matching rule."""

    tag: str = Field(..., description="Tag that needs a folder rule")
    folder: str = Field(..., description="Suggested target folder path")
    note_count: int = Field(..., description="Number of unprocessed notes using this tag")
    example_notes: list[str] = Field(
        default_factory=list, description="Example note titles using this tag"
    )
    existing_folder_match: bool = Field(
        False, description="Whether the folder suggestion matched an existing folder"
    )


class MoveResult(BaseModel):
    """Result of moving a note to a folder."""

    file: str = Field(..., description="Filename")
    from_folder: str = Field(..., description="Source folder")
    to_folder: str = Field(..., description="Destination folder")
    timestamp: datetime = Field(default_factory=datetime.now, description="When move occurred")
    tags: list[str] = Field(default_factory=list, description="Note tags")
    matched_tag: str | None = Field(None, description="Tag that matched")
    score: float = Field(0.0, description="Selection score")
    success: bool = Field(..., description="Whether move succeeded")
    error: str | None = Field(None, description="Error message if failed")


def validate_folder_path(folder: str, vault_path: Path, max_depth: int = 4) -> None:
    """Validate folder path is safe and within vault.

    Args:
        folder: Folder path to validate
        vault_path: Path to Obsidian vault
        max_depth: Maximum allowed depth (default: 4)

    Raises:
        PathTraversalError: If path contains traversal sequences or escapes vault
        InvalidRulesError: If folder depth exceeds maximum
    """
    # Check for path traversal patterns
    if ".." in folder or folder.startswith("/") or folder.startswith("\\"):
        raise PathTraversalError(
            f"Folder path contains invalid sequences: {folder}. "
            "Paths cannot contain '..', or start with '/' or '\\'."
        )

    # Validate depth
    depth = folder.count("/") + 1
    if depth > max_depth:
        raise InvalidRulesError(
            f"Folder path '{folder}' has {depth} levels, maximum is {max_depth}"
        )

    # Verify resolved path is within vault
    try:
        test_path = (vault_path / folder).resolve()
        vault_resolved = vault_path.resolve()

        if not str(test_path).startswith(str(vault_resolved)):
            raise PathTraversalError(
                f"Folder path escapes vault: {folder} resolves outside {vault_path}"
            )
    except Exception as e:
        raise PathTraversalError(f"Failed to validate folder path '{folder}': {e}") from e


def load_folder_rules(vault_path: Path) -> dict[str, str]:
    """Load and validate folder rules from vault root.

    Args:
        vault_path: Path to Obsidian vault

    Returns:
        Dictionary mapping tags to folder paths

    Raises:
        InvalidRulesError: If rules file doesn't exist or is invalid
        PathTraversalError: If any folder path is unsafe
    """
    rules_path = vault_path / "folder_rules.json"

    if not rules_path.exists():
        raise InvalidRulesError(
            f"No folder_rules.json found at {rules_path}. "
            "Create this file in your vault root with tag-to-folder mappings."
        )

    try:
        rules = json.loads(rules_path.read_text())
        if not isinstance(rules, dict):
            raise InvalidRulesError("folder_rules.json must contain a JSON object (dict)")

        # Validate all folder paths immediately
        for _tag, folder in rules.items():
            validate_folder_path(folder, vault_path)

        return rules
    except json.JSONDecodeError as e:
        raise InvalidRulesError(f"Invalid JSON in folder_rules.json: {e}") from e


def normalize_tags(tags_value: str | list[str] | None) -> list[str]:
    """Normalize tags to always be a list.

    The frontmatter library returns tags as string if single value,
    or list if multiple values. This function normalizes to always return a list.

    Args:
        tags_value: Tags from frontmatter (can be str, list, or None)

    Returns:
        List of tag strings
    """
    if tags_value is None:
        return []
    if isinstance(tags_value, str):
        return [tags_value]
    if isinstance(tags_value, list):
        return [str(tag) for tag in tags_value]  # Ensure all items are strings
    return []


def calculate_folder_scores(
    tags: list[str], rules: dict[str, str]
) -> dict[str, tuple[float, list[str]]]:
    """Calculate scores for each possible destination folder.

    Scoring logic:
    - Each matching tag adds +1 to folder score
    - Deeper folder paths get +0.1 bonus per level (more specific)
    - Multiple tags matching the same folder accumulate score

    Args:
        tags: List of note tags
        rules: Tag-to-folder mapping

    Returns:
        Dictionary mapping folder paths to (score, matched_tags) tuples
    """
    folder_scores: dict[str, tuple[float, list[str]]] = {}

    for tag in tags:
        if tag in rules:
            folder = rules[tag]

            # Get current score and matched tags for this folder
            if folder in folder_scores:
                current_score, matched_tags = folder_scores[folder]
                # Add 1.0 for this additional matching tag
                current_score += 1.0
                matched_tags.append(tag)
            else:
                # First match for this folder
                current_score = 1.0
                # Add specificity bonus (0.1 per path level) - only once per folder
                depth = folder.count("/")
                current_score += depth * 0.1
                matched_tags = [tag]

            folder_scores[folder] = (current_score, matched_tags)

    return folder_scores


def find_best_folder(tags: list[str], rules: dict[str, str]) -> tuple[str | None, list[str], float]:
    """Find highest-scoring folder for given tags.

    Args:
        tags: List of note tags
        rules: Tag-to-folder mapping

    Returns:
        Tuple of (best_folder, matched_tags, score) or (None, [], 0.0) if no match
    """
    if not tags:
        return None, [], 0.0

    scores = calculate_folder_scores(tags, rules)
    if not scores:
        return None, [], 0.0

    # Find folder with highest score
    best_folder = max(scores.items(), key=lambda x: x[1][0])
    folder_path, (score, matched_tags) = best_folder

    return folder_path, matched_tags, score


def scan_inbox_notes(
    vault_path: Path, inbox_folder: str, rules: dict[str, str]
) -> tuple[list[NoteToMove], list[str]]:
    """Scan inbox for notes and determine target folders.

    Args:
        vault_path: Path to Obsidian vault
        inbox_folder: Inbox folder name
        rules: Tag-to-folder mapping

    Returns:
        Tuple of (notes_to_move, failed_files) where failed_files are filenames
        that couldn't be parsed
    """
    inbox_path = vault_path / inbox_folder
    if not inbox_path.exists():
        return [], []

    notes_to_move: list[NoteToMove] = []
    failed_files: list[str] = []

    for note_file in inbox_path.glob("*.md"):
        try:
            # Parse frontmatter to get tags
            parsed = parse_frontmatter(note_file)
            frontmatter = parsed["frontmatter"]

            title = frontmatter.get("title", note_file.stem)

            # Normalize tags to always be a list (handles str, list, or None)
            tags_raw = frontmatter.get("tags")
            tags = normalize_tags(tags_raw)

            # Find best folder for this note
            best_folder, matched_tags, score = find_best_folder(tags, rules)

            # Only include notes that have a match
            if best_folder:
                notes_to_move.append(
                    NoteToMove(
                        file_path=note_file,
                        title=title,
                        tags=tags,
                        best_folder=best_folder,
                        matched_tags=matched_tags,
                        score=score,
                    )
                )

        except Exception:
            # Track files that can't be parsed
            failed_files.append(note_file.name)
            continue

    return notes_to_move, failed_files


def _format_tag_segment_as_folder(segment: str) -> str:
    """Convert one tag path segment to a readable folder segment."""
    words = [word for word in segment.replace("_", "-").replace(" ", "-").split("-") if word]
    acronyms = {
        "ai",
        "api",
        "ar",
        "eu",
        "hr",
        "it",
        "lgbtq",
        "llm",
        "ml",
        "nlp",
        "pdf",
        "smr",
        "ui",
        "ux",
        "vr",
    }
    formatted_words = [
        word.upper() if word.lower() in acronyms else word.capitalize() for word in words
    ]
    return " ".join(formatted_words)


def _tokenize_rule_text(value: str) -> set[str]:
    """Tokenize tags and folder paths for lightweight matching."""
    return {
        token.lower() for token in re.split(r"[^A-Za-z0-9]+", value) if token and len(token) > 1
    }


def _collect_existing_folders(vault_path: Path, rules: dict[str, str]) -> list[str]:
    """Collect candidate folders from existing rules and vault directories."""
    folders = set(rules.values())

    # All four names start with ".", so the startswith(".") filter below
    # subsumes them; mutating this set is equivalent.
    ignored = {".git", ".obsidian", ".kai", ".trash"}  # pragma: no mutate
    for path in vault_path.rglob("*"):
        if not path.is_dir():
            continue

        try:
            relative = path.relative_to(vault_path)
        except ValueError:
            continue  # pragma: no mutate (rglob only yields descendants of vault_path)

        parts = relative.parts
        if not parts or any(part.startswith(".") or part in ignored for part in parts):
            continue
        if len(parts) > 4:
            continue

        folders.add(relative.as_posix())

    return sorted(folders)


def _find_existing_folder_match(tag: str, existing_folders: list[str]) -> str | None:
    """Find the best existing folder for a tag using token overlap."""
    tag_tokens = _tokenize_rule_text(tag)
    if not tag_tokens:
        return None

    best_folder: str | None = None  # pragma: no mutate
    best_score = 0.0

    for folder in existing_folders:
        folder_tokens = _tokenize_rule_text(folder)
        if not folder_tokens:
            continue

        overlap = tag_tokens & folder_tokens
        if not overlap:
            continue

        score = len(overlap) / len(tag_tokens)
        if tag.lower().replace("-", " ") in folder.lower().replace("-", " "):
            score += 0.5

        if score > best_score:
            best_score = score
            best_folder = folder

    return best_folder if best_score >= 0.5 else None


def suggest_folder_for_tag(tag: str, existing_folders: list[str] | None = None) -> str:
    """Suggest a folder path from a tag.

    Existing folders are preferred when their words match the tag.
    Hierarchical tags keep their hierarchy, while kebab/underscore words become
    readable folder names. For example, ``ai/llm-agents`` becomes
    ``AI/LLM Agents``.
    """
    if existing_folders:
        existing_match = _find_existing_folder_match(tag, existing_folders)
        if existing_match:
            return existing_match

    segments = [
        _format_tag_segment_as_folder(segment)
        for segment in tag.strip("#/").split("/")
        if segment and segment not in {".", ".."}
    ]
    return "/".join(segments) if segments else "Uncategorized"


def _create_move_result(
    note: NoteToMove,
    vault_path: Path,
    success: bool,
    error: str | None = None,
) -> MoveResult:
    """Create a MoveResult with consistent field mapping.

    Args:
        note: Note being moved
        vault_path: Path to Obsidian vault
        success: Whether move succeeded
        error: Error message if failed

    Returns:
        MoveResult object
    """
    # Calculate source folder relative to vault
    try:
        from_folder = str(note.file_path.parent.relative_to(vault_path))
    except ValueError:
        # If file is not in vault, use folder name
        from_folder = note.file_path.parent.name

    return MoveResult(
        file=note.file_path.name,
        from_folder=from_folder,
        to_folder=note.best_folder or "",
        timestamp=datetime.now(),
        tags=note.tags,
        matched_tag=note.matched_tag,
        score=note.score,
        success=success,
        error=error,
    )


def move_note(note: NoteToMove, vault_path: Path, dry_run: bool = False) -> MoveResult:
    """Move note file to destination folder.

    Args:
        note: Note to move
        vault_path: Path to Obsidian vault
        dry_run: If True, simulate move without executing

    Returns:
        MoveResult with success status and details
    """
    # Handle case where note has no best folder (shouldn't happen in practice)
    if not note.best_folder:
        return _create_move_result(
            note, vault_path, success=False, error="No target folder determined"
        )

    dest_folder = vault_path / note.best_folder
    dest_file = dest_folder / note.file_path.name

    # Security: verify resolved destination is within vault
    try:
        resolved_dest = dest_file.resolve()
        resolved_vault = vault_path.resolve()

        if not str(resolved_dest).startswith(str(resolved_vault)):
            return _create_move_result(
                note, vault_path, success=False, error="Path traversal detected"
            )
    except Exception as e:
        return _create_move_result(
            note, vault_path, success=False, error=f"Path validation failed: {e}"
        )

    try:
        if not dry_run:
            # Create destination folder if needed
            dest_folder.mkdir(parents=True, exist_ok=True)

            # If file exists in destination, remove it (overwrite behavior)
            if dest_file.exists():
                dest_file.unlink()

            # Move file
            shutil.move(str(note.file_path), str(dest_file))

        return _create_move_result(note, vault_path, success=True)

    except Exception as e:
        return _create_move_result(note, vault_path, success=False, error=str(e))


def track_move(result: MoveResult, vault_path: Path) -> None:
    """Track move in folder mappings log using JSONL format.

    Args:
        result: Move result to track
        vault_path: Path to Obsidian vault

    Raises:
        FolderOrganizerError: If tracking fails
    """
    tracking_file = vault_path / ".kai" / "folder_mappings.jsonl"

    try:
        # Create .kai directory if needed
        tracking_file.parent.mkdir(parents=True, exist_ok=True)

        # Append as single JSON line (JSONL format)
        with tracking_file.open("a", encoding="utf-8") as f:
            json.dump(
                {
                    "file": result.file,
                    "from": result.from_folder,
                    "to": result.to_folder,
                    "timestamp": result.timestamp.isoformat(),
                    "tags": result.tags,
                    "matched_tag": result.matched_tag,
                    "score": result.score,
                    "success": result.success,
                    "error": result.error,
                },
                f,
            )
            f.write("\n")
    except Exception as e:
        raise FolderOrganizerError(f"Failed to track move: {e}") from e
