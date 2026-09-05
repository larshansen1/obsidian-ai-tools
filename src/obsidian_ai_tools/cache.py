"""Cache management for YouTube video metadata and transcripts."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .models import VideoMetadata


class CachedVideo(BaseModel):
    """Cached video metadata with cache timestamp and provider info."""

    metadata: VideoMetadata
    cached_at: datetime = Field(default_factory=datetime.now)
    provider: str = Field(..., description="Provider used: 'unofficial' | 'decodo' | 'youtube_api'")


class VideoCache:
    """File-based cache for YouTube video metadata."""

    def __init__(self, cache_dir: Path):
        """Initialize cache.

        Args:
            cache_dir: Directory to store cache files
        """
        self.cache_dir = cache_dir / "youtube"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_path(self, video_id: str) -> Path:
        """Get cache file path for a video ID."""
        return self.cache_dir / f"{video_id}.json"

    def get(self, video_id: str) -> VideoMetadata | None:
        """Retrieve cached video metadata if valid.

        Args:
            video_id: YouTube video ID

        Returns:
            VideoMetadata if found and not expired, None otherwise
        """
        cache_path = self._get_cache_path(video_id)

        if not cache_path.exists():
            return None

        try:
            # Load cached data
            with cache_path.open("r") as f:
                data = json.load(f)

            cached_video = CachedVideo(**data)

            return cached_video.metadata

        except (json.JSONDecodeError, ValueError, KeyError):
            # Corrupted cache file - remove it. `missing_ok` only differs when the
            # file vanished between the exists() guard above and unlink(), an
            # impossible TOCTOU race in single-threaded use (mutants equivalent).
            cache_path.unlink(missing_ok=True)  # pragma: no mutate
            return None

    def set(self, video_id: str, metadata: VideoMetadata, provider: str) -> None:
        """Cache video metadata.

        Args:
            video_id: YouTube video ID
            metadata: Video metadata to cache
            provider: Provider that fetched the data
        """
        cache_path = self._get_cache_path(video_id)

        cached_video = CachedVideo(metadata=metadata, provider=provider)

        # Write to cache file
        with cache_path.open("w") as f:
            json.dump(
                cached_video.model_dump(mode="json"),
                f,
                indent=2,
                # model_dump(mode="json") is fully JSON-serializable, so default
                # is never invoked; removing/mutating it is equivalent.
                default=str,  # pragma: no mutate
            )

    def invalidate(self, video_id: str) -> bool:
        """Remove cached data for a video.

        Args:
            video_id: YouTube video ID

        Returns:
            True if cache was removed, False if it didn't exist
        """
        cache_path = self._get_cache_path(video_id)
        if cache_path.exists():
            cache_path.unlink()
            return True
        return False

    def clear(self) -> int:
        """Clear all cached videos.

        Returns:
            Number of cache files removed
        """
        count = 0
        for cache_file in self.cache_dir.glob("*.json"):
            cache_file.unlink()
            count += 1
        return count

    def stats(self) -> dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dictionary with cache stats
        """
        total = 0
        corrupted = 0

        for cache_file in self.cache_dir.glob("*.json"):
            total += 1
            try:
                with cache_file.open("r") as f:
                    data = json.load(f)
                CachedVideo(**data)
            except (json.JSONDecodeError, ValueError, KeyError):
                corrupted += 1

        return {
            "total_files": total,
            "corrupted": corrupted,
            "cache_dir": str(self.cache_dir),
        }
