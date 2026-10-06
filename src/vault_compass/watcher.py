"""Keep compass.duckdb fresh while `compass serve` runs (D7).

One refresh when the server starts, then one after each burst of vault
changes. A refresh only re-reads files whose mtime changed.
"""

import asyncio
import logging
import time
from pathlib import Path

from watchfiles import Change, awatch

from .config import CompassSettings
from .notes import refresh_notes_incremental
from .topics import TopicsError, load_topics

logger = logging.getLogger(__name__)

# Wait this long after the last change so one ingest (several writes) is one refresh.
DEBOUNCE_MS = 500


def refresh_once(settings: CompassSettings) -> None:
    """Run one incremental refresh; log the outcome. Never raises: a failed refresh must
    not stop the server, the API keeps serving the last good data.
    """
    started = time.perf_counter()
    try:
        try:
            topics = load_topics(settings.compass_topics_path)
        except TopicsError as e:
            logger.warning("topics not loaded, topic tables left as they are: %s", e)
            topics = None
        stats = refresh_notes_incremental(
            settings.obsidian_vault_path, settings.compass_db_path, topics
        )
    except Exception:
        logger.exception("compass refresh failed")
        return
    logger.info(
        "compass refresh: %d notes (%d added, %d updated, %d removed, full=%s) in %.2fs",
        stats.note_count,
        stats.added,
        stats.updated,
        stats.removed,
        stats.full,
        time.perf_counter() - started,
    )


def is_relevant_change(vault: Path, topics_path: Path, path: str) -> bool:
    """True for a visible .md file in the vault, or the topics file."""
    changed = Path(path)
    if changed == topics_path:
        return True
    try:
        rel = changed.relative_to(vault)
    except ValueError:
        return False
    return changed.suffix == ".md" and not any(part.startswith(".") for part in rel.parts)


async def watch_vault(settings: CompassSettings, stop_event: asyncio.Event) -> None:
    """Refresh after each batch of relevant changes until `stop_event` is set."""
    vault = settings.obsidian_vault_path
    topics_path = settings.compass_topics_path

    def keep(_change: Change, path: str) -> bool:
        return is_relevant_change(vault, topics_path, path)

    async for _changes in awatch(
        vault,
        topics_path.parent if topics_path.parent.is_dir() else vault,
        watch_filter=keep,
        debounce=DEBOUNCE_MS,
        stop_event=stop_event,
    ):
        await asyncio.to_thread(refresh_once, settings)
