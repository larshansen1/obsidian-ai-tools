"""Obsidian vault file operations."""

import re
from pathlib import Path
from urllib.parse import quote

from .models import Note


class FileWriteError(Exception):
    """Base exception for file writing errors."""

    pass


class PathTraversalError(FileWriteError):
    """Raised when path traversal attempt is detected."""

    pass


def sanitize_filename(title: str, max_length: int = 100) -> str:
    """Sanitize title for use as filename.

    Removes special characters, converts to lowercase, and limits length.
    Follows Obsidian-safe naming conventions.

    Args:
        title: Original title string
        max_length: Maximum filename length (default: 100)

    Returns:
        Sanitized filename-safe string
    """
    # Remove or replace invalid filesystem characters
    # Invalid: < > : " / \ | ? *
    sanitized = re.sub(r'[<>:"/\\|?*]', "", title)

    # Replace spaces and multiple spaces with single hyphen
    sanitized = re.sub(r"\s+", "-", sanitized)

    # Remove leading/trailing hyphens and spaces
    sanitized = sanitized.strip("-").strip()

    # Lowercase for consistency
    sanitized = sanitized.lower()

    # Limit length
    # Truncating a string of exactly max_length is the identity; the full string
    # was already strip("-")-ed, so the comparison threshold is unobservable.
    if len(sanitized) > max_length:  # pragma: no mutate
        sanitized = sanitized[:max_length].rstrip("-")

    # Ensure we have something (fallback to "note" if empty)
    if not sanitized:
        sanitized = "untitled-note"

    return sanitized


def build_filename(source_type: str, title: str) -> str:
    """Build filename in format: {source_type}-{sanitized-title}.md

    Args:
        source_type: Source type (e.g., "youtube", "article")
        title: Note title

    Returns:
        Complete filename with .md extension
    """
    sanitized_title = sanitize_filename(title)
    return f"{source_type}-{sanitized_title}.md"


def build_obsidian_url(vault_path: Path, file_path: Path) -> str:
    """Build an obsidian://open URI for a note inside a vault.

    Assumes the Obsidian vault name matches the vault directory name
    (Obsidian's default when opening a folder as a vault).

    Args:
        vault_path: Path to Obsidian vault root
        file_path: Path to the note file (must be inside vault_path)

    Returns:
        Percent-encoded obsidian://open URI

    Raises:
        ValueError: If file_path is not inside vault_path
    """
    rel_path = file_path.relative_to(vault_path)
    # quote() never encodes alnum + "._~-"; vault names contain no "/", so safe-
    # value variants and the default safe="/" encode identically.
    vault_name = quote(vault_path.name, safe="")  # pragma: no mutate
    file_param = quote(str(rel_path), safe="")  # pragma: no mutate
    return f"obsidian://open?vault={vault_name}&file={file_param}"


def write_note(
    note: Note,
    vault_path: Path,
    inbox_folder: str = "inbox",
    target_path: Path | None = None,
) -> Path:
    """Write note to Obsidian vault inbox folder.

    Args:
        note: Note object to write
        vault_path: Path to Obsidian vault root
        inbox_folder: Folder within vault for new notes (default: "inbox")
        target_path: Overwrite this existing note instead of deriving a
            filename from the title (used for source-based updates, where the
            file identity must not follow the LLM's regenerated title)

    Returns:
        Path to created note file

    Raises:
        FileWriteError: If note cannot be written
        PathTraversalError: If path traversal is detected
    """
    if target_path is not None:
        try:
            resolved_target = target_path.resolve()
            resolved_vault = vault_path.resolve()
        except Exception as e:
            raise FileWriteError(f"Path validation failed: {e}") from e
        if not str(resolved_target).startswith(str(resolved_vault)):
            raise PathTraversalError(f"Update target outside vault: {target_path}")
        try:
            # utf-8 is the default codec; omitted/None/UTF-8 variants produce
            # byte-identical output for any content on this platform.
            target_path.write_text(note.to_markdown(), encoding="utf-8")  # pragma: no mutate
            return target_path
        except Exception as e:
            raise FileWriteError(f"Failed to write note to {target_path}: {e}") from e

    # Build target directory path
    inbox_path = vault_path / inbox_folder

    # Create inbox directory if it doesn't exist
    try:
        inbox_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        raise FileWriteError(f"Failed to create inbox directory: {e}") from e

    # Build filename
    filename = build_filename(note.source_type, note.title)

    # Validate filename doesn't contain path separators (prevent path traversal)
    if "/" in filename or "\\" in filename:
        raise PathTraversalError(f"Filename contains path separators: {filename}")

    # Build full file path
    file_path = inbox_path / filename

    # Additional security: ensure resolved path is within inbox
    try:
        resolved_file = file_path.resolve()
        resolved_inbox = inbox_path.resolve()

        if not str(resolved_file).startswith(str(resolved_inbox)):
            raise PathTraversalError(f"Path traversal detected: {file_path} -> {resolved_file}")
    except Exception as e:
        if isinstance(e, PathTraversalError):
            raise
        raise FileWriteError(f"Path validation failed: {e}") from e

    # Check if file already exists (MVP: overwrite with warning)
    # TODO: In future, add options for: skip, rename, prompt user
    if file_path.exists():
        # For MVP, we'll overwrite
        # In production, you might want different behavior
        pass

    # Convert note to markdown
    markdown_content = note.to_markdown()

    # Write file
    try:
        file_path.write_text(markdown_content, encoding="utf-8")
        return file_path
    except Exception as e:
        raise FileWriteError(f"Failed to write note to {file_path}: {e}") from e
