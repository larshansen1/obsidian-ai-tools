"""Exception classes for YouTube transcript fetching."""


class TranscriptError(Exception):
    """Base exception for transcript fetching errors."""

    pass


class InvalidYouTubeURLError(TranscriptError):
    """Raised when URL is not a valid YouTube URL."""

    pass


class TranscriptUnavailableError(TranscriptError):
    """Raised when transcript is not available for the video."""

    pass
