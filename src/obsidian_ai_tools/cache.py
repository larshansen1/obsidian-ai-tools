"""Cache management for YouTube video metadata and transcripts."""

import json
from datetime import datetime, timedelta
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

    def __init__(self, cache_dir: Path, ttl_hours: int = 168):
        """Initialize cache.

        Args:
            cache_dir: Directory to store cache files
            ttl_hours: Time-to-live in hours (default: 168 = 7 days)
        """
        self.cache_dir = cache_dir / "youtube"
        self.ttl = timedelta(hours=ttl_hours)
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

            # Check if expired
            age = datetime.now() - cached_video.cached_at
            if age > self.ttl:
                # Cache expired - remove file
                cache_path.unlink()
                return None

            return cached_video.metadata

        except (json.JSONDecodeError, ValueError, KeyError):
            # Corrupted cache file - remove it
            cache_path.unlink(missing_ok=True)
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
                default=str,
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
        valid = 0
        expired = 0

        for cache_file in self.cache_dir.glob("*.json"):
            total += 1
            try:
                with cache_file.open("r") as f:
                    data = json.load(f)
                cached_video = CachedVideo(**data)
                age = datetime.now() - cached_video.cached_at

                if age <= self.ttl:
                    valid += 1
                else:
                    expired += 1
            except (json.JSONDecodeError, ValueError, KeyError):
                expired += 1

        return {
            "total_files": total,
            "valid": valid,
            "expired": expired,
            "ttl_hours": self.ttl.total_seconds() / 3600,
            "cache_dir": str(self.cache_dir),
        }
