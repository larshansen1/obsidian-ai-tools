"""Smart re-processing module for upgrading notes with new prompt versions."""

import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class RefreshError(Exception):
    """Base exception for refresh-related errors."""

    pass


class SourceUnavailableError(RefreshError):
    """Raised when source content cannot be re-fetched."""

    pass


class RefreshCandidate(BaseModel):
    """Note eligible for refresh."""

    file_path: Path
    title: str
    current_prompt_version: str
    target_prompt_version: str
    source_url: str
    source_type: str
    created_at: datetime | None = None

    class Config:
        arbitrary_types_allowed = True


class RefreshResult(BaseModel):
    """Result of a refresh operation."""

    file_path: Path
    success: bool
    backup_path: Path | None = None
    error: str | None = None
    cost_usd: float = 0.0

    class Config:
        arbitrary_types_allowed = True


class RefreshSummary(BaseModel):
    """Summary of a batch refresh operation."""

    total_candidates: int = 0
    refreshed: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)
    total_cost_usd: float = 0.0


def parse_frontmatter(file_path: Path) -> dict[str, Any]:
    """Extract frontmatter metadata from a markdown note.

    Args:
        file_path: Path to the markdown file

    Returns:
        Dictionary of frontmatter key-value pairs

    Raises:
        ValueError: If file cannot be parsed
    """
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        raise ValueError(f"Cannot read file: {e}") from e

    # Check for frontmatter delimiters
    if not content.startswith("---"):
        return {}

    # Find end of frontmatter
    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return {}

    frontmatter_text = content[4 : end_match.start() + 3]

    # Parse YAML-style frontmatter (simple key: value parsing)
    metadata: dict[str, Any] = {}

    for line in frontmatter_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Handle key: value pairs
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()

            # Remove quotes if present
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]

            # Handle lists (tags: [a, b, c])
            if value.startswith("[") and value.endswith("]"):
                items = value[1:-1].split(",")
                metadata[key] = [item.strip().strip("'\"") for item in items if item.strip()]
            else:
                metadata[key] = value

    return metadata


def find_refresh_candidates(
    vault_path: Path,
    target_version: str,
    tag: str | None = None,
    current_version: str | None = None,
    since_days: int | None = None,
) -> list[RefreshCandidate]:
    """Find notes eligible for refresh based on filters.

    Args:
        vault_path: Path to the Obsidian vault
        target_version: Target prompt version to upgrade to
        tag: Optional tag filter
        current_version: Only refresh notes with this specific prompt version
        since_days: Only notes older than this many days

    Returns:
        List of notes eligible for refresh
    """
    candidates: list[RefreshCandidate] = []
    cutoff_date = datetime.now() - timedelta(days=since_days) if since_days else None

    # Scan all markdown files in vault
    for md_file in vault_path.rglob("*.md"):
        # Skip hidden directories and backup files
        if any(part.startswith(".") for part in md_file.parts):
            continue
        if md_file.name.endswith(".backup.md"):
            continue

        try:
            metadata = parse_frontmatter(md_file)
        except Exception:
            continue

        # Must have source_url and source_type for refresh
        source_url = metadata.get("source_url")
        source_type = metadata.get("source_type")
        if not source_url or not source_type:
            continue

        # Check prompt_version
        note_version = metadata.get("prompt_version", "")
        if not note_version:
            continue

        # Skip if already at target version
        if note_version == target_version:
            continue

        # Filter by current version if specified
        if current_version and note_version != current_version:
            continue

        # Check that target prompt version is compatible with source type
        # Prompt versions are named like "youtube_v2", "article_v1", "pdf_v1", etc.
        # The prefix should match the source type
        target_prefix = target_version.split("_")[0]
        source_type_mapping = {
            "youtube": "youtube",
            "web": "article",  # web sources use "article_v1" prompts
            "pdf": "pdf",
            "file": "markdown",  # file sources use "markdown_v1" prompts
        }
        expected_prefix = source_type_mapping.get(source_type, source_type)
        if target_prefix != expected_prefix:
            continue

        # Filter by tag if specified
        if tag:
            tags = metadata.get("tags", [])
            if isinstance(tags, str):
                tags = [tags]
            if tag not in tags:
                continue

        # Filter by date if specified
        if cutoff_date:
            created_str = metadata.get("created")
            if created_str:
                try:
                    # Parse ISO format datetime
                    created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    # Make cutoff_date timezone-aware if created is
                    if created.tzinfo is not None:
                        from datetime import UTC

                        cutoff_aware = cutoff_date.replace(tzinfo=UTC)
                        if created > cutoff_aware:
                            continue
                    elif created > cutoff_date:
                        continue
                except (ValueError, TypeError):
                    pass  # Can't parse date, include the note

        title = metadata.get("title", md_file.stem)

        candidates.append(
            RefreshCandidate(
                file_path=md_file,
                title=title,
                current_prompt_version=note_version,
                target_prompt_version=target_version,
                source_url=source_url,
                source_type=source_type,
            )
        )

    return candidates


def estimate_refresh_cost(
    candidates: list[RefreshCandidate], model: str = "openai/gpt-4o-mini"
) -> float:
    """Estimate total LLM cost for refreshing candidates.

    Uses rough token estimates based on source type.

    Args:
        candidates: List of refresh candidates
        model: LLM model identifier

    Returns:
        Estimated cost in USD
    """
    # Rough cost estimates per source type (input + output tokens)
    # Based on gpt-4o-mini pricing: ~$0.15/1M input, ~$0.60/1M output
    cost_per_type = {
        "youtube": 0.02,  # ~8K tokens avg
        "web": 0.01,  # ~4K tokens avg
        "pdf": 0.03,  # ~12K tokens avg
        "file": 0.01,  # ~4K tokens avg
    }

    total = 0.0
    for candidate in candidates:
        total += cost_per_type.get(candidate.source_type, 0.02)

    return total


def create_backup(file_path: Path) -> Path:
    """Create a backup of a note before refreshing.

    Args:
        file_path: Path to the original note

    Returns:
        Path to the backup file
    """
    backup_path = file_path.with_suffix(".backup.md")

    # If backup already exists, add timestamp
    if backup_path.exists():
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = file_path.with_suffix(f".backup_{timestamp}.md")

    shutil.copy2(file_path, backup_path)
    return backup_path


def refresh_note(
    candidate: RefreshCandidate,
    vault_path: Path,
    model: str,
    api_key: str,
    create_backup_file: bool = True,
) -> RefreshResult:
    """Re-process a single note with a new prompt version.

    Args:
        candidate: The note to refresh
        vault_path: Path to the Obsidian vault
        model: LLM model identifier
        api_key: OpenRouter API key
        create_backup_file: Whether to create backup before overwriting

    Returns:
        RefreshResult with success status and details
    """
    from .llm import NoteGenerationError, generate_note
    from .obsidian import write_note
    from .providers.factory import ProviderFactory

    backup_path = None

    try:
        # Step 1: Create backup if requested
        if create_backup_file:
            backup_path = create_backup(candidate.file_path)

        # Step 2: Re-fetch source content
        try:
            provider = ProviderFactory.get_provider(candidate.source_url)
            metadata = provider.ingest(candidate.source_url)
        except Exception as e:
            raise SourceUnavailableError(f"Cannot fetch source: {e}") from e

        # Step 3: Generate new note with target prompt version
        note = generate_note(
            metadata=metadata,
            model=model,
            api_key=api_key,
            vault_path=vault_path,
            prompt_version=candidate.target_prompt_version,
        )

        # Step 4: Write note (overwriting original)
        # Get the folder from the original path
        original_folder = candidate.file_path.parent
        inbox_folder = original_folder.relative_to(vault_path)

        # Write to same location
        write_note(note, vault_path, str(inbox_folder))

        # Get cost from observability (approximate)
        cost = estimate_refresh_cost([candidate], model)

        return RefreshResult(
            file_path=candidate.file_path,
            success=True,
            backup_path=backup_path,
            cost_usd=cost,
        )

    except SourceUnavailableError as e:
        return RefreshResult(
            file_path=candidate.file_path,
            success=False,
            backup_path=backup_path,
            error=f"Source unavailable: {e}",
        )
    except NoteGenerationError as e:
        return RefreshResult(
            file_path=candidate.file_path,
            success=False,
            backup_path=backup_path,
            error=f"LLM generation failed: {e}",
        )
    except Exception as e:
        return RefreshResult(
            file_path=candidate.file_path,
            success=False,
            backup_path=backup_path,
            error=f"Unexpected error: {e}",
        )


def refresh_batch(
    candidates: list[RefreshCandidate],
    vault_path: Path,
    model: str,
    api_key: str,
    create_backup_file: bool = True,
) -> RefreshSummary:
    """Refresh multiple notes in batch.

    Args:
        candidates: List of notes to refresh
        vault_path: Path to the Obsidian vault
        model: LLM model identifier
        api_key: OpenRouter API key
        create_backup_file: Whether to create backups

    Returns:
        RefreshSummary with aggregate results
    """
    summary = RefreshSummary(total_candidates=len(candidates))

    for candidate in candidates:
        result = refresh_note(
            candidate=candidate,
            vault_path=vault_path,
            model=model,
            api_key=api_key,
            create_backup_file=create_backup_file,
        )

        if result.success:
            summary.refreshed += 1
            summary.total_cost_usd += result.cost_usd
        else:
            summary.skipped += 1
            if result.error:
                summary.errors.append(f"{candidate.file_path.name}: {result.error}")

    return summary
