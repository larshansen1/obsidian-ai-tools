"""Tests for cache functionality."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import obsidian_ai_tools.cache as cache_module
from obsidian_ai_tools.cache import VideoCache
from obsidian_ai_tools.models import VideoMetadata


class _FixedDatetime:
    """Deterministic datetime.now() replacement for expiry tests."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


@pytest.fixture
def cache_dir(tmp_path: Path) -> Path:
    """Create a temporary cache directory."""
    return tmp_path / "test_cache"


@pytest.fixture
def cache(cache_dir: Path) -> VideoCache:
    """Create a VideoCache instance."""
    return VideoCache(cache_dir, ttl_hours=1)


@pytest.fixture
def sample_metadata() -> VideoMetadata:
    """Create sample video metadata."""
    return VideoMetadata(
        video_id="test123",
        title="Test Video",
        url="https://youtube.com/watch?v=test123",
        transcript="This is a test transcript",
        channel_name="Test Channel",
        source_language="en",
    )


class TestVideoCache:
    """Tests for VideoCache class."""

    def test_cache_initialization(self, cache_dir: Path) -> None:
        """Test cache directory is created."""
        _ = VideoCache(cache_dir)
        assert (cache_dir / "youtube").exists()
        assert (cache_dir / "youtube").is_dir()

    def test_default_ttl_hours(self, cache_dir: Path) -> None:
        """The default TTL is 168 hours (7 days)."""
        cache = VideoCache(cache_dir)
        assert cache.ttl == timedelta(hours=168)

    def test_reuses_existing_cache_dir(self, cache_dir: Path) -> None:
        """Initializing over an existing cache directory succeeds."""
        (cache_dir / "youtube").mkdir(parents=True)
        cache = VideoCache(cache_dir)
        assert (cache_dir / "youtube").is_dir()
        assert cache.cache_dir == cache_dir / "youtube"

    def test_cache_miss(self, cache: VideoCache) -> None:
        """Test cache miss returns None."""
        result = cache.get("nonexistent")
        assert result is None

    def test_cache_hit(self, cache: VideoCache, sample_metadata: VideoMetadata) -> None:
        """Test cache hit returns cached metadata."""
        cache.set("test123", sample_metadata, "unofficial")
        result = cache.get("test123")

        assert result is not None
        assert result.video_id == sample_metadata.video_id
        assert result.title == sample_metadata.title
        assert result.transcript == sample_metadata.transcript

    def test_cache_expiration(self, cache_dir: Path, sample_metadata: VideoMetadata) -> None:
        """Test cache expires after TTL."""
        # Create cache with 0 hour TTL (immediate expiration)
        cache = VideoCache(cache_dir, ttl_hours=0)

        cache.set("test123", sample_metadata, "unofficial")
        # Even immediate retrieval should fail with 0 TTL
        result = cache.get("test123")
        assert result is None

    def test_cache_file_structure(self, cache: VideoCache, sample_metadata: VideoMetadata) -> None:
        """Test cache file is created correctly."""
        cache.set("test123", sample_metadata, "unofficial")

        cache_file = cache.cache_dir / "test123.json"
        assert cache_file.exists()

        # Verify file structure
        with cache_file.open("r") as f:
            data = json.load(f)

        assert "metadata" in data
        assert "cached_at" in data
        assert "provider" in data
        assert data["provider"] == "unofficial"

    def test_invalidate(self, cache: VideoCache, sample_metadata: VideoMetadata) -> None:
        """Test cache invalidation."""
        cache.set("test123", sample_metadata, "unofficial")
        assert cache.get("test123") is not None

        result = cache.invalidate("test123")
        assert result is True
        assert cache.get("test123") is None

    def test_invalidate_nonexistent(self, cache: VideoCache) -> None:
        """Test invalidating nonexistent cache."""
        result = cache.invalidate("nonexistent")
        assert result is False

    def test_clear(self, cache: VideoCache, sample_metadata: VideoMetadata) -> None:
        """Test clearing all cache."""
        cache.set("test1", sample_metadata, "unofficial")
        cache.set("test2", sample_metadata, "decodo")
        cache.set("test3", sample_metadata, "unofficial")

        count = cache.clear()
        assert count == 3
        assert cache.get("test1") is None
        assert cache.get("test2") is None
        assert cache.get("test3") is None

    def test_stats(
        self, cache: VideoCache, sample_metadata: VideoMetadata, cache_dir: Path
    ) -> None:
        """Test cache statistics."""
        cache.set("test1", sample_metadata, "unofficial")
        cache.set("test2", sample_metadata, "decodo")

        stats = cache.stats()
        assert stats["total_files"] == 2
        assert stats["valid"] == 2
        assert stats["expired"] == 0
        assert stats["ttl_hours"] == 1

    def test_corrupted_cache_file(self, cache: VideoCache, cache_dir: Path) -> None:
        """Test handling of corrupted cache file."""
        # Create corrupted cache file
        cache_file = cache.cache_dir / "corrupted.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text("invalid json{{{")

        # Should return None and remove corrupted file
        result = cache.get("corrupted")
        assert result is None
        assert not cache_file.exists()

    def test_set_writes_pretty_printed_json(
        self, cache: VideoCache, sample_metadata: VideoMetadata
    ) -> None:
        """Cache files are written with 2-space indentation."""
        cache.set("test123", sample_metadata, "unofficial")
        raw = (cache.cache_dir / "test123.json").read_text()
        assert '\n  "' in raw

    def test_set_stores_iso_timestamp(
        self, cache: VideoCache, sample_metadata: VideoMetadata
    ) -> None:
        """cached_at is stored as an ISO-8601 string."""
        cache.set("test123", sample_metadata, "unofficial")
        data = json.loads((cache.cache_dir / "test123.json").read_text())
        assert "T" in data["cached_at"]

    def test_get_not_expired_at_exact_ttl(
        self, cache_dir: Path, sample_metadata: VideoMetadata, monkeypatch
    ) -> None:
        """A cache entry aged exactly TTL hours is still valid."""
        cache = VideoCache(cache_dir, ttl_hours=1)
        fixed_now = datetime(2026, 1, 1, 12, 0, 0)
        cache.set("test123", sample_metadata, "unofficial")
        cache_file = cache.cache_dir / "test123.json"
        data = json.loads(cache_file.read_text())
        data["cached_at"] = (fixed_now - timedelta(hours=1)).isoformat()
        cache_file.write_text(json.dumps(data))
        monkeypatch.setattr(cache_module, "datetime", _FixedDatetime(fixed_now))

        result = cache.get("test123")

        assert result is not None
        assert cache_file.exists()

    def test_get_expired_removes_file(
        self, cache_dir: Path, sample_metadata: VideoMetadata, monkeypatch
    ) -> None:
        """Expired entries are removed when read."""
        cache = VideoCache(cache_dir, ttl_hours=1)
        fixed_now = datetime(2026, 1, 1, 12, 0, 0)
        cache.set("test123", sample_metadata, "unofficial")
        cache_file = cache.cache_dir / "test123.json"
        data = json.loads(cache_file.read_text())
        data["cached_at"] = (fixed_now - timedelta(hours=2)).isoformat()
        cache_file.write_text(json.dumps(data))
        monkeypatch.setattr(cache_module, "datetime", _FixedDatetime(fixed_now))

        result = cache.get("test123")

        assert result is None
        assert not cache_file.exists()

    def test_stats_counts_with_fixed_time(
        self, cache_dir: Path, sample_metadata: VideoMetadata, monkeypatch
    ) -> None:
        """Stats count valid, expired and corrupt entries correctly."""
        cache = VideoCache(cache_dir, ttl_hours=1)
        fixed_now = datetime(2026, 1, 1, 12, 0, 0)
        for vid, age_hours in [("valid1", 1), ("expired1", 2), ("expired2", 3)]:
            cache.set(vid, sample_metadata, "unofficial")
            path = cache.cache_dir / f"{vid}.json"
            data = json.loads(path.read_text())
            data["cached_at"] = (fixed_now - timedelta(hours=age_hours)).isoformat()
            path.write_text(json.dumps(data))
        for vid in ("corrupt1", "corrupt2"):
            (cache.cache_dir / f"{vid}.json").write_text("{invalid json", encoding="utf-8")
        monkeypatch.setattr(cache_module, "datetime", _FixedDatetime(fixed_now))

        stats = cache.stats()

        assert stats["total_files"] == 5
        assert stats["valid"] == 1
        assert stats["expired"] == 4
        assert stats["ttl_hours"] == 1.0
        assert stats["cache_dir"] == str(cache.cache_dir)

    def test_provider_tracking(self, cache: VideoCache, sample_metadata: VideoMetadata) -> None:
        """Test provider is tracked correctly."""
        cache.set("test1", sample_metadata, "unofficial")
        cache.set("test2", sample_metadata, "decodo")
        cache.set("test3", sample_metadata, "youtube_api")

        # Verify provider is stored correctly
        for vid, provider in [
            ("test1", "unofficial"),
            ("test2", "decodo"),
            ("test3", "youtube_api"),
        ]:
            cache_file = cache.cache_dir / f"{vid}.json"
            with cache_file.open("r") as f:
                data = json.load(f)
            assert data["provider"] == provider
