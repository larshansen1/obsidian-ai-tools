"""Base provider interface for content ingestion."""

from abc import ABC, abstractmethod
from typing import Any

from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..models import ArticleMetadata, VideoMetadata


class BaseProvider(ABC):
    """Abstract base class for all content providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name (e.g. 'youtube', 'web', 'file')."""
        pass

    @abstractmethod
    def validate(self, source: str) -> bool:
        """Check if this provider can handle the given source (URL or path).

        Args:
            source: Input URL or file path

        Returns:
            True if supported, False otherwise
        """
        pass

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_not_exception_type((ValueError, FileNotFoundError, IsADirectoryError)),
        reraise=True,
    )
    def ingest(self, source: str, **kwargs: Any) -> VideoMetadata | ArticleMetadata:
        """Ingest content from source with automatic retries.

        Delegate actual ingestion to _ingest implementation.

        Args:
            source: Input URL or file path
            **kwargs: Additional arguments for specific providers

        Returns:
            Standardized metadata object

        Raises:
            ValueError: If source is invalid
            RuntimeError: If ingestion fails after retries
        """
        return self._ingest(source, **kwargs)

    @abstractmethod
    def _ingest(self, source: str, **kwargs: Any) -> VideoMetadata | ArticleMetadata:
        """Internal ingestion implementation.

        Args:
            source: Input URL or file path
            **kwargs: Additional arguments

        Returns:
            Standardized metadata object
        """
        pass
