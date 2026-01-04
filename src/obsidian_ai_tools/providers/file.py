"""Local file ingestion provider."""

import logging
from pathlib import Path
from typing import Any

from ..models import ArticleMetadata
from .base import BaseProvider

logger = logging.getLogger(__name__)


class FileProvider(BaseProvider):
    """Provider for reading content from local files."""

    @property
    def name(self) -> str:
        return "file"

    def validate(self, source: str) -> bool:
        """Check if source is a valid file path."""
        # Simple heuristic: starts with ./, /, or exists
        path = Path(source)
        return path.exists() or source.startswith(("/", "./", "../"))

    def _ingest(self, source: str, **kwargs: Any) -> ArticleMetadata:
        """Read content from a local file.

        Args:
            source: Path to the file

        Returns:
            ArticleMetadata object
        """
        path = Path(source).resolve()
        logger.info(f"Reading file: {path}")

        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        if not path.is_file():
            raise IsADirectoryError(f"Path is a directory: {path}")

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.error(f"Failed to decode file {path} as UTF-8")
            raise

        return ArticleMetadata(
            title=path.stem.replace("_", " ").title(),
            url=f"file://{path}",
            author="Local File",
            site_name="Local Filesystem",
            content=content,
            published_date=None,
        )
