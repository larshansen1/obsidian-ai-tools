"""AI-powered flashcard generation for Obsidian Spaced Repetition."""

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from openai import OpenAI

logger = logging.getLogger(__name__)


class FlashcardError(Exception):
    """Raised when flashcard generation fails."""


@dataclass
class FlashcardCandidate:
    """A note that does not yet have a corresponding flashcard file."""

    file_path: Path
    title: str
    created_at: datetime | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class FlashcardSummary:
    """Aggregate result of a batch flashcard generation run."""

    total_candidates: int = 0
    generated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    total_cost_usd: float = 0.0


def _parse_frontmatter(content: str) -> dict[str, object]:
    """Extract YAML frontmatter fields from markdown content.

    Handles both inline lists (``tags: [a, b]``) and block lists
    (``tags:\\n  - a\\n  - b``).
    """
    if not content.startswith("---"):
        return {}
    end = re.search(r"\n---\s*\n", content[3:])
    if not end:
        return {}
    fm_text = content[4 : end.start() + 3]
    result: dict[str, object] = {}
    current_list_key: str | None = None
    for line in fm_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Block-list item under the current key
        if stripped.startswith("- ") and current_list_key:
            item = stripped[2:].strip().strip("\"'")
            cast: list[str] = result.setdefault(current_list_key, [])  # type: ignore[assignment]
            cast.append(item)
            continue
        current_list_key = None
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip("\"'")
            if val == "":
                # Key with no inline value — expect block-list items next
                current_list_key = key
            elif val.startswith("[") and val.endswith("]"):
                result[key] = [v.strip().strip("\"'") for v in val[1:-1].split(",") if v.strip()]
            else:
                result[key] = val
    return result


def _strip_frontmatter(content: str) -> str:
    """Return note body with the YAML frontmatter block removed."""
    if not content.startswith("---"):
        return content
    end = re.search(r"\n---\s*\n", content[3:])
    if not end:
        return content
    return content[end.start() + 4 + 3 :].lstrip("\n")


def note_tags(note_path: Path) -> list[str]:
    """Return the frontmatter tags list for a note, or [] if none."""
    try:
        content = note_path.read_text(encoding="utf-8")
    except Exception:
        return []
    metadata = _parse_frontmatter(content)
    raw = metadata.get("tags", [])
    if isinstance(raw, list):
        return [str(t) for t in raw]
    return [str(raw)] if raw else []


def compute_deck(tags: list[str], tag_filter: str | None = None) -> str:
    """Return the SR plugin deck tag for a note.

    Uses *tag_filter* as the sub-deck when provided (batch mode with --tag),
    otherwise falls back to the note's first tag, or the root ``flashcards``
    deck when no tags exist.
    """
    if tag_filter:
        return f"flashcards/{tag_filter}"
    if tags:
        return f"flashcards/{tags[0]}"
    return "flashcards"


def find_flashcard_candidates(
    vault_path: Path,
    tag: str | None = None,
    since_days: int | None = None,
    folder: str | None = None,
    flashcards_folder: str = "Flashcards",
    force: bool = False,
) -> list[FlashcardCandidate]:
    """Find notes eligible for flashcard generation.

    Args:
        vault_path: Absolute path to the Obsidian vault.
        tag: Only include notes that carry this tag.
        since_days: Only include notes created within the last N days.
        folder: Restrict scan to this vault sub-folder.
        flashcards_folder: Name of the flashcards root folder (excluded from scan).
        force: When True, include notes that already have a flashcard file.

    Returns:
        Ordered list of notes eligible for flashcard generation.
    """
    candidates: list[FlashcardCandidate] = []
    cutoff = datetime.now() - timedelta(days=since_days) if since_days else None
    scan_root = vault_path / folder if folder else vault_path
    flashcards_root = vault_path / flashcards_folder

    for md_file in scan_root.rglob("*.md"):
        if any(part.startswith(".") for part in md_file.parts):
            continue
        if md_file.name.endswith(".backup.md"):
            continue
        # Skip files that live inside the flashcards folder
        try:
            md_file.relative_to(flashcards_root)
            continue
        except ValueError:
            pass

        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            logger.warning("Cannot read %s — skipping", md_file)
            continue

        metadata = _parse_frontmatter(content)

        if tag:
            raw = metadata.get("tags", [])
            tags_list: list[str] = (
                [str(t) for t in raw]
                if isinstance(raw, list)
                else ([raw] if isinstance(raw, str) else [])
            )
            if tag not in tags_list:
                continue

        created_at: datetime | None = None
        created_str = metadata.get("created")
        if isinstance(created_str, str):
            try:
                created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        if cutoff and created_at is not None:
            if created_at.tzinfo is not None:
                from datetime import UTC

                cutoff_aware = cutoff.replace(tzinfo=UTC)
                if created_at < cutoff_aware:
                    continue
            elif created_at < cutoff:
                continue

        # Skip if flashcard file already exists (unless --force)
        rel = md_file.relative_to(vault_path)
        if not force and (flashcards_root / rel).exists():
            continue

        title = str(metadata.get("title", md_file.stem))
        raw_tags = metadata.get("tags", [])
        note_tags_list: list[str] = (
            [str(t) for t in raw_tags]
            if isinstance(raw_tags, list)
            else ([raw_tags] if isinstance(raw_tags, str) else [])
        )
        candidates.append(
            FlashcardCandidate(
                file_path=md_file, title=title, created_at=created_at, tags=note_tags_list
            )
        )

    return candidates


def generate_flashcards(
    note_path: Path,
    count: int,
    model: str,
    api_key: str,
) -> tuple[list[dict[str, str]], float]:
    """Generate flashcards for a single note using the LLM.

    Args:
        note_path: Absolute path to the source note.
        count: Maximum number of cards to generate.
        model: OpenRouter model identifier.
        api_key: OpenRouter API key.

    Returns:
        Tuple of (list of {"question": str, "answer": str} dicts, cost_usd).

    Raises:
        FlashcardError: If the LLM call or JSON parsing fails.
    """
    from .llm import load_prompt_template

    content = note_path.read_text(encoding="utf-8")
    metadata = _parse_frontmatter(content)
    title = str(metadata.get("title", note_path.stem))
    body = _strip_frontmatter(content)

    template = load_prompt_template("flashcard_v1")
    prompt = template.format(title=title, content=body, count=count)

    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            extra_body={"usage": {"include": True}},
        )
    except Exception as e:
        raise FlashcardError(f"LLM call failed: {e}") from e

    usage = response.usage
    cost_usd = 0.0
    if usage and hasattr(usage, "cost"):
        cost_usd = float(usage.cost)

    response_text = (response.choices[0].message.content or "").strip()

    if "```json" in response_text:
        start = response_text.find("```json") + 7
        end = response_text.find("```", start)
        json_str = response_text[start:end].strip()
    elif "```" in response_text:
        start = response_text.find("```") + 3
        end = response_text.find("```", start)
        json_str = response_text[start:end].strip()
    else:
        json_str = response_text

    try:
        cards = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise FlashcardError(f"Failed to parse LLM response as JSON: {e}") from e

    if not isinstance(cards, list):
        raise FlashcardError(f"Expected JSON array from LLM, got {type(cards).__name__}")

    return cards, cost_usd


def write_flashcard_file(
    note_path: Path,
    cards: list[dict[str, str]],
    vault_path: Path,
    flashcards_folder: str = "Flashcards",
    force: bool = False,
    deck: str = "flashcards",
) -> Path | None:
    """Write a flashcard file in Obsidian Spaced Repetition format.

    The flashcard file mirrors the source note's folder structure under
    ``flashcards_folder``.  Existing files are skipped unless *force* is set.

    Args:
        note_path: Absolute path to the source note.
        cards: List of {"question": str, "answer": str} dicts.
        vault_path: Absolute path to the Obsidian vault.
        flashcards_folder: Root folder name for flashcard files.
        force: Overwrite an existing flashcard file when True.
        deck: SR plugin deck tag (e.g. ``flashcards`` or ``flashcards/ai``).

    Returns:
        The path of the written file, or None if skipped.
    """
    rel = note_path.relative_to(vault_path)
    flashcard_path = vault_path / flashcards_folder / rel

    if flashcard_path.exists() and not force:
        logger.warning(
            "Flashcard file already exists (use --force to overwrite): %s", flashcard_path
        )
        return None

    note_stem = rel.with_suffix("").as_posix()
    note_title = note_path.stem
    today = datetime.now().date().isoformat()

    lines = [
        "---",
        "tags:",
        f"  - {deck}",
        f'source: "[[{note_stem}]]"',
        f"generated: {today}",
        "---",
        "",
        f"> Source: [[{note_title}]]",
        "",
    ]

    valid_cards = [
        (card.get("question", "").strip(), card.get("answer", "").strip())
        for card in cards
        if card.get("question", "").strip() and card.get("answer", "").strip()
    ]

    for i, (question, answer) in enumerate(valid_cards):
        lines.append(f"{question} :: {answer}")
        if i < len(valid_cards) - 1:
            lines.append("")

    lines.append("")

    flashcard_path.parent.mkdir(parents=True, exist_ok=True)
    flashcard_path.write_text("\n".join(lines), encoding="utf-8")
    return flashcard_path


def estimate_flashcard_cost(
    candidates: list[FlashcardCandidate],
    count: int = 5,
) -> float:
    """Estimate the total LLM cost for generating flashcards.

    Uses a rough figure of $0.001 per card.

    Args:
        candidates: Notes to generate flashcards for.
        count: Cards requested per note.

    Returns:
        Estimated cost in USD.
    """
    return len(candidates) * count * 0.001
