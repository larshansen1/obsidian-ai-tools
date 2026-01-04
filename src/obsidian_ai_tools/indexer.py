"""Vault indexing functionality for searching Obsidian notes."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel, Field


class NoteMetadata(BaseModel):
    """Metadata extracted from a note file."""

    file_path: Path = Field(..., description="Absolute path to the note file")
    title: str = Field(..., description="Note title from frontmatter")
    tags: list[str] = Field(default_factory=list, description="Tags from frontmatter")
    created: datetime | None = Field(None, description="Creation timestamp")
    author: str | None = Field(None, description="Content author")
    source_url: str | None = Field(None, description="Original source URL")
    source_type: str | None = Field(None, description="Source type (e.g., youtube)")
    content: str = Field(..., description="Full note content (without frontmatter)")
    modified_time: float = Field(..., description="File modification timestamp")


class VaultIndex(BaseModel):
    """Index of all notes in the vault."""

    notes: list[NoteMetadata] = Field(default_factory=list)
    index_path: Path = Field(..., description="Path to the index JSON file")
    last_updated: datetime = Field(default_factory=datetime.now)

    def save(self) -> None:
        """Save index to JSON file."""
        self.index_path.parent.mkdir(parents=True, exist_ok=True)

        # Convert to dict for JSON serialization
        data = {
            "notes": [
                {
                    **note.model_dump(),
                    "file_path": str(note.file_path),
                    "created": note.created.isoformat() if note.created else None,
                }
                for note in self.notes
            ],
            "last_updated": self.last_updated.isoformat(),
        }

        self.index_path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, index_path: Path) -> "VaultIndex | None":
        """Load index from JSON file if it exists."""
        if not index_path.exists():
            return None

        try:
            data = json.loads(index_path.read_text())
            notes = [
                NoteMetadata(
                    **{
                        **note_data,
                        "file_path": Path(note_data["file_path"]),
                        "created": (
                            datetime.fromisoformat(note_data["created"])
                            if note_data.get("created")
                            else None
                        ),
                    }
                )
                for note_data in data["notes"]
            ]
            return cls(
                notes=notes,
                index_path=index_path,
                last_updated=datetime.fromisoformat(data["last_updated"]),
            )
        except Exception:
            # If index is corrupted, return None to trigger rebuild
            return None


def parse_frontmatter(file_path: Path) -> dict[str, Any]:
    """Parse frontmatter from a markdown file.

    Args:
        file_path: Path to the markdown file

    Returns:
        Dictionary with frontmatter data and content

    Raises:
        Exception: If file cannot be read or parsed
    """
    try:
        post = frontmatter.load(file_path)
        return {
            "frontmatter": dict(post.metadata),
            "content": post.content,
        }
    except Exception as e:
        raise Exception(f"Failed to parse frontmatter from {file_path}: {e}") from e


def scan_vault(vault_path: Path, folder: str | None = "inbox") -> list[NoteMetadata]:
    """Scan vault folder for markdown files and extract metadata.

    Args:
        vault_path: Path to the Obsidian vault
        folder: Subfolder to scan (default: inbox). If None, scans entire vault recursively.

    Returns:
        List of NoteMetadata objects
    """
    # Determine scan path and glob pattern
    if folder is None:
        # Scan entire vault recursively
        scan_path = vault_path
        glob_pattern = "**/*.md"
    else:
        # Scan specific folder (non-recursive)
        scan_path = vault_path / folder
        glob_pattern = "*.md"

    if not scan_path.exists():
        return []

    notes: list[NoteMetadata] = []

    for md_file in scan_path.glob(glob_pattern):
        try:
            # Parse frontmatter
            parsed = parse_frontmatter(md_file)
            fm = parsed["frontmatter"]
            content = parsed["content"]

            # Extract metadata
            title = fm.get("title", md_file.stem)
            tags = fm.get("tags", [])

            # Parse created timestamp
            created = None
            if "created" in fm:
                try:
                    created = datetime.fromisoformat(str(fm["created"]))
                except (ValueError, TypeError):
                    pass

            # Get file modification time
            modified_time = md_file.stat().st_mtime

            notes.append(
                NoteMetadata(
                    file_path=md_file,
                    title=title,
                    tags=tags,
                    created=created,
                    author=fm.get("author"),
                    source_url=fm.get("source_url"),
                    source_type=fm.get("source_type"),
                    content=content,
                    modified_time=modified_time,
                )
            )
        except Exception:
            # Skip files that can't be parsed
            continue

    return notes


def build_index(
    vault_path: Path,
    folder: str | None = "inbox",
    index_path: Path | None = None,
    force_rebuild: bool = False,
) -> VaultIndex:
    """Build or load vault index.

    Args:
        vault_path: Path to the Obsidian vault
        folder: Subfolder to index (default: inbox). If None, indexes entire vault recursively.
        index_path: Path to save/load index (default: vault/.kai/vault_index.json)
        force_rebuild: Force rebuild even if cached index exists

    Returns:
        VaultIndex with all notes
    """
    if index_path is None:
        index_path = vault_path / ".kai" / "vault_index.json"

    # Try to load existing index
    if not force_rebuild:
        existing_index = VaultIndex.load(index_path)
        if existing_index:
            # Check if vault has been modified since last index
            if folder is None:
                # Scanning entire vault
                scan_path = vault_path
                glob_pattern = "**/*.md"
            else:
                # Scanning specific folder
                scan_path = vault_path / folder
                glob_pattern = "*.md"

            if scan_path.exists():
                latest_mod = max(
                    (f.stat().st_mtime for f in scan_path.glob(glob_pattern)),
                    default=0,
                )
                index_timestamp = existing_index.last_updated.timestamp()

                # If no files changed, return cached index
                if latest_mod <= index_timestamp:
                    return existing_index

    # Rebuild index
    notes = scan_vault(vault_path, folder)
    index = VaultIndex(notes=notes, index_path=index_path)
    index.save()

    return index
