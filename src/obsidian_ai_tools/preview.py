"""Preview module for URL triage before ingestion.

Provides metadata-only extraction, cost estimation, and reading list management
without full content extraction or LLM calls.
"""

import json
import logging
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================


class PreviewError(Exception):
    """Base exception for preview failures."""

    pass


class UnsupportedURLError(PreviewError):
    """URL type not supported for preview."""

    pass


# =============================================================================
# Models
# =============================================================================


class PreviewInfo(BaseModel):
    """Preview information for a URL."""

    url: str = Field(..., description="Source URL")
    source_type: str = Field(..., description="Source type: youtube, web, pdf")
    title: str = Field(..., description="Content title")
    content_length: int = Field(..., description="Estimated word count")
    duration: str | None = Field(None, description="Duration for videos (e.g. '23:15')")
    estimated_cost_usd: float = Field(..., description="Estimated LLM cost in USD")
    key_topics: list[str] = Field(default_factory=list, description="Top keywords")
    fetched_at: datetime = Field(
        default_factory=datetime.now, description="When preview was fetched"
    )


class ReadingListEntry(BaseModel):
    """Entry in the reading list."""

    url: str = Field(..., description="Source URL")
    preview: PreviewInfo = Field(..., description="Preview information")
    added_at: datetime = Field(default_factory=datetime.now, description="When added to list")
    status: str = Field(default="pending", description="Status: pending, ingested, skipped")


# =============================================================================
# Cost Estimation
# =============================================================================


def estimate_cost(content_length: int, source_type: str = "web") -> float:
    """Estimate LLM cost based on content length.

    Uses Claude 3.5 Sonnet pricing as reference:
    - Input: $3/M tokens
    - Output: $15/M tokens (assume ~500 tokens for note generation)

    Args:
        content_length: Word count of content
        source_type: Source type (currently unused, may affect pricing later)

    Returns:
        Estimated cost in USD, rounded to 4 decimal places
    """
    # Approximate tokens = words * 1.3 (average for English)
    input_tokens = int(content_length * 1.3)
    output_tokens = 500  # Typical note generation output

    # Claude 3.5 Sonnet pricing: $3/M input, $15/M output
    cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000

    return round(cost, 4)


# =============================================================================
# Topic Extraction
# =============================================================================

# Common stop words to filter out
STOP_WORDS = frozenset([
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "been",
    "be", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "must", "shall", "can", "need",
    "this", "that", "these", "those", "i", "you", "he", "she", "it", "we",
    "they", "what", "which", "who", "whom", "when", "where", "why", "how",
    "all", "each", "every", "both", "few", "more", "most", "other", "some",
    "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too",
    "very", "just", "about", "into", "over", "after", "before", "between",
    "through", "during", "above", "below", "up", "down", "out", "off", "if",
    "then", "else", "because", "while", "although", "though", "even",
    "also", "still", "here", "there", "now", "then", "many", "much",
])


def extract_topics(text: str, top_n: int = 5) -> list[str]:
    """Extract key topics from text using word frequency.

    Simple keyword extraction without requiring external libraries.
    Filters stop words and returns most frequent meaningful terms.

    Args:
        text: Text content to analyze
        top_n: Number of top topics to return

    Returns:
        List of top keywords
    """
    if not text:
        return []

    # Normalize text: lowercase, remove punctuation, split
    words = re.findall(r'\b[a-z]{3,}\b', text.lower())

    # Filter stop words and short words
    filtered = [w for w in words if w not in STOP_WORDS and len(w) > 3]

    # Count frequencies
    counter = Counter(filtered)

    # Return top N
    return [word for word, _ in counter.most_common(top_n)]


# =============================================================================
# Reading List Persistence
# =============================================================================


def _get_reading_list_path(vault_path: Path) -> Path:
    """Get path to reading list file."""
    kai_dir = vault_path / ".kai"
    kai_dir.mkdir(parents=True, exist_ok=True)
    return kai_dir / "reading_list.jsonl"


def save_to_reading_list(entry: ReadingListEntry, vault_path: Path) -> None:
    """Save entry to reading list.

    Appends to JSONL file in .kai directory.

    Args:
        entry: Reading list entry to save
        vault_path: Path to Obsidian vault
    """
    list_path = _get_reading_list_path(vault_path)

    try:
        with open(list_path, "a", encoding="utf-8") as f:
            f.write(entry.model_dump_json() + "\n")
        logger.info(f"Saved to reading list: {entry.url}")
    except Exception as e:
        logger.error(f"Failed to save to reading list: {e}")
        raise PreviewError(f"Failed to save to reading list: {e}") from e


def load_reading_list(vault_path: Path) -> list[ReadingListEntry]:
    """Load reading list from vault.

    Args:
        vault_path: Path to Obsidian vault

    Returns:
        List of reading list entries
    """
    list_path = _get_reading_list_path(vault_path)

    if not list_path.exists():
        return []

    entries = []
    try:
        with open(list_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    entries.append(ReadingListEntry.model_validate(data))
    except Exception as e:
        logger.warning(f"Error loading reading list: {e}")

    return entries


def update_reading_list_status(url: str, status: str, vault_path: Path) -> bool:
    """Update status of an entry in reading list.

    Rewrites the entire file with updated status.

    Args:
        url: URL to update
        status: New status (pending, ingested, skipped)
        vault_path: Path to Obsidian vault

    Returns:
        True if entry was found and updated
    """
    entries = load_reading_list(vault_path)
    updated = False

    for entry in entries:
        if entry.url == url:
            entry.status = status
            updated = True
            break

    if updated:
        list_path = _get_reading_list_path(vault_path)
        with open(list_path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(entry.model_dump_json() + "\n")

    return updated


# =============================================================================
# Preview Generation
# =============================================================================


def detect_source_type(url: str) -> str:
    """Detect source type from URL.

    Args:
        url: URL to analyze

    Returns:
        Source type: youtube, web, or pdf

    Raises:
        UnsupportedURLError: If URL type cannot be determined
    """
    lower_url = url.lower()

    if "youtube.com" in lower_url or "youtu.be" in lower_url:
        return "youtube"
    elif lower_url.endswith(".pdf") or "/pdf/" in lower_url:
        return "pdf"
    elif lower_url.startswith(("http://", "https://")):
        return "web"
    else:
        raise UnsupportedURLError(f"Cannot determine source type for: {url}")


def generate_preview(url: str) -> PreviewInfo:
    """Generate preview information for a URL.

    Fetches metadata only without full content extraction or LLM calls.

    Args:
        url: URL to preview

    Returns:
        PreviewInfo with metadata and estimated cost

    Raises:
        PreviewError: If preview generation fails
        UnsupportedURLError: If URL type not supported
    """
    source_type = detect_source_type(url)

    try:
        if source_type == "youtube":
            return _preview_youtube(url)
        elif source_type == "pdf":
            return _preview_pdf(url)
        else:  # web
            return _preview_web(url)
    except PreviewError:
        raise
    except Exception as e:
        logger.error(f"Preview failed for {url}: {e}")
        raise PreviewError(f"Failed to generate preview: {e}") from e


def _preview_youtube(url: str) -> PreviewInfo:
    """Generate preview for YouTube URL."""
    from .youtube import YouTubeClient, extract_video_id

    client = YouTubeClient()
    video_id = extract_video_id(url)

    # Fetch metadata only (no transcript)
    metadata = client._fetch_metadata(video_id)

    title = metadata.get("title", "Unknown Video")
    # Estimate word count from duration if available
    # Average speaking rate: ~150 words/minute
    duration_str = metadata.get("duration")
    estimated_words = 5000  # Default estimate

    if duration_str:
        # Parse duration (could be "H:MM:SS" or "MM:SS")
        parts = duration_str.split(":")
        total_minutes = 0
        if len(parts) == 3:  # H:MM:SS
            total_minutes = int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 2:  # MM:SS
            total_minutes = int(parts[0])
        elif len(parts) == 1:  # SS or just minutes
            total_minutes = int(parts[0]) // 60 if int(parts[0]) > 60 else 1
        estimated_words = int(total_minutes * 150)

    return PreviewInfo(
        url=url,
        source_type="youtube",
        title=title,
        content_length=estimated_words,
        duration=duration_str,
        estimated_cost_usd=estimate_cost(estimated_words, "youtube"),
        key_topics=extract_topics(title),
    )


def _preview_web(url: str) -> PreviewInfo:
    """Generate preview for web URL."""
    # Use light-weight extraction for preview
    try:
        # Try to get basic metadata
        import requests
        from bs4 import BeautifulSoup

        response = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (compatible; ObsidianAI/1.0)"
        })
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")

        # Extract title
        title = "Unknown Article"
        if soup.title:
            title = soup.title.string or title
        elif soup.find("h1"):
            title = soup.find("h1").get_text(strip=True)

        # Estimate content length from text
        text = soup.get_text(separator=" ", strip=True)
        word_count = len(text.split())

        # Extract topics from first portion of text
        preview_text = text[:2000] if len(text) > 2000 else text
        topics = extract_topics(preview_text)

        return PreviewInfo(
            url=url,
            source_type="web",
            title=title[:200],  # Truncate long titles
            content_length=word_count,
            estimated_cost_usd=estimate_cost(word_count, "web"),
            key_topics=topics,
        )

    except Exception as e:
        logger.warning(f"Web preview failed: {e}")
        raise PreviewError(f"Failed to preview web page: {e}") from e


def _preview_pdf(url: str) -> PreviewInfo:
    """Generate preview for PDF URL."""
    import requests

    try:
        # For remote PDFs, fetch headers to get size
        if url.startswith(("http://", "https://")):
            response = requests.head(url, timeout=10, allow_redirects=True)
            content_length = int(response.headers.get("Content-Length", 0))

            # Estimate pages from file size (rough: ~100KB per page)
            estimated_pages = max(1, content_length // 100_000)

            # Estimate words (rough: ~500 words per page)
            estimated_words = estimated_pages * 500

            # Try to extract title from URL
            title = url.split("/")[-1].replace(".pdf", "").replace("-", " ").replace("_", " ")
        else:
            # Local PDF
            path = Path(url)
            if not path.exists():
                raise PreviewError(f"PDF not found: {url}")

            file_size = path.stat().st_size
            estimated_pages = max(1, file_size // 100_000)
            estimated_words = estimated_pages * 500
            title = path.stem.replace("-", " ").replace("_", " ")

        return PreviewInfo(
            url=url,
            source_type="pdf",
            title=title[:200],
            content_length=estimated_words,
            estimated_cost_usd=estimate_cost(estimated_words, "pdf"),
            key_topics=[],  # Can't extract topics without reading content
        )

    except PreviewError:
        raise
    except Exception as e:
        logger.warning(f"PDF preview failed: {e}")
        raise PreviewError(f"Failed to preview PDF: {e}") from e


# =============================================================================
# Formatting
# =============================================================================


def format_preview_terminal(preview: PreviewInfo) -> str:
    """Format preview for terminal display.

    Args:
        preview: PreviewInfo to format

    Returns:
        Terminal-friendly string with emoji
    """
    lines = [f'→ Preview: "{preview.title}"']

    # Source line
    source_desc = preview.source_type.capitalize()
    if preview.duration:
        source_desc += f" ({preview.duration})"
    lines.append(f"  Source: {source_desc}")

    # Content length
    lines.append(f"  Content: ~{preview.content_length:,} words")

    # Cost
    lines.append(f"  Estimated cost: ${preview.estimated_cost_usd:.4f}")

    # Topics
    if preview.key_topics:
        topics_str = ", ".join(preview.key_topics)
        lines.append(f"  Key topics: {topics_str}")

    return "\n".join(lines)


def format_preview_json(preview: PreviewInfo) -> str:
    """Format preview as JSON.

    Args:
        preview: PreviewInfo to format

    Returns:
        JSON string
    """
    return preview.model_dump_json(indent=2)
