"""Factory for creating content providers."""

from .base import BaseProvider
from .file import FileProvider
from .pdf import PDFProvider
from .web import WebProvider
from .youtube import YouTubeProvider


class ProviderFactory:
    """Factory for selecting and creating content providers."""

    _providers: list[type[BaseProvider]] = [
        YouTubeProvider,
        PDFProvider,  # PDF before File so .pdf files use PDF provider
        FileProvider,
        WebProvider,  # Web is catch-all for URLs, so put last
    ]

    @classmethod
    def get_provider(cls, source: str) -> BaseProvider:
        """Get appropriate provider for the source.

        Args:
            source: Input URL or file path

        Returns:
            Instantiated provider capable of handling the source

        Raises:
            ValueError: If no provider can handle the source
        """
        for provider_cls in cls._providers:
            provider = provider_cls()
            if provider.validate(source):
                return provider

        raise ValueError(f"No provider found for source: {source}")
