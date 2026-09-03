"""Tests for the local file ingestion provider."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from obsidian_ai_tools.models import ArticleMetadata
from obsidian_ai_tools.providers.file import FileProvider

# _ingest is called directly instead of the tenacity-retried ingest
# entrypoint so failing paths do not trigger real retry backoff sleeps.


class TestFileProviderName:
    """Name property exposed by the provider."""

    def test_name_is_file(self) -> None:
        assert FileProvider().name == "file"


class TestFileProviderValidate:
    """Source validation heuristics."""

    def test_validate_existing_relative_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An existing relative path validates even without a path prefix."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "notes.txt").write_text("content")

        assert FileProvider().validate("notes.txt") is True

    def test_validate_absolute_prefix_without_existing_file(self) -> None:
        assert FileProvider().validate("/definitely/not/a/real/file.txt") is True

    def test_validate_dot_slash_prefix(self) -> None:
        assert FileProvider().validate("./missing_file.txt") is True

    def test_validate_dot_dot_prefix(self) -> None:
        assert FileProvider().validate("../missing_file.txt") is True

    def test_validate_rejects_unrelated_source(self) -> None:
        assert FileProvider().validate("definitely_not_a_real_source_xyz") is False


class TestFileProviderIngest:
    """Content reading, metadata construction and attempt recording."""

    def test_ingest_success_builds_article_metadata(self, tmp_path: Path) -> None:
        target = tmp_path / "my_notes_doc.txt"
        target.write_text("Hello world content\nLine two", encoding="utf-8")

        db = MagicMock()
        provider = FileProvider()
        with patch("obsidian_ai_tools.providers.file.get_db", return_value=db):
            result = provider._ingest(str(target))

        assert isinstance(result, ArticleMetadata)
        assert result.title == "My Notes Doc"
        assert result.url == f"file://{target.resolve()}"
        assert result.author == "Local File"
        assert result.site_name == "Local Filesystem"
        assert result.published_date is None
        assert result.content == "Hello world content\nLine two"

        db.record_provider_attempt.assert_called_once()
        args: tuple[Any, ...] = db.record_provider_attempt.call_args.args
        assert args[0] == "file"
        assert args[1] == "primary"
        assert args[2] == "success"
        assert isinstance(args[3], float)
        assert db.record_provider_attempt.call_args.kwargs == {"url": str(target)}

    def test_ingest_logs_read_start(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        target = tmp_path / "info.txt"
        target.write_text("content", encoding="utf-8")

        db = MagicMock()
        provider = FileProvider()
        with caplog.at_level("INFO"):
            with patch("obsidian_ai_tools.providers.file.get_db", return_value=db):
                provider._ingest(str(target))

        assert "Reading file:" in caplog.text

    def test_ingest_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        provider = FileProvider()
        missing = tmp_path / "nope.txt"

        with pytest.raises(FileNotFoundError, match=r"File not found: .*nope"):
            provider._ingest(str(missing))

    def test_ingest_directory_raises_is_a_directory(self, tmp_path: Path) -> None:
        provider = FileProvider()
        directory = tmp_path / "a_directory"
        directory.mkdir()

        with pytest.raises(IsADirectoryError, match=r"Path is a directory: .*a_directory"):
            provider._ingest(str(directory))

    def test_ingest_undecodable_file_records_failure(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        target = tmp_path / "binary.bin"
        target.write_bytes(b"\xff\xfe\x00\x01 binary bytes")

        db = MagicMock()
        provider = FileProvider()
        with caplog.at_level("ERROR"):
            with patch("obsidian_ai_tools.providers.file.get_db", return_value=db):
                with pytest.raises(UnicodeDecodeError):
                    provider._ingest(str(target))

        assert "Failed to decode file" in caplog.text
        db.record_provider_attempt.assert_called_once()
        args: tuple[Any, ...] = db.record_provider_attempt.call_args.args
        assert args[0:2] == ("file", "primary")
        assert args[2] == "failure"
        assert isinstance(args[3], float)
        assert args[4:] == ("UnicodeDecodeError", str(target))

    def test_ingest_db_failure_does_not_break_success_path(self, tmp_path: Path) -> None:
        target = tmp_path / "ok.txt"
        target.write_text("content", encoding="utf-8")

        provider = FileProvider()
        db = MagicMock()
        db.record_provider_attempt.side_effect = RuntimeError("db down")
        with patch("obsidian_ai_tools.providers.file.get_db", return_value=db):
            result = provider._ingest(str(target))

        assert result.content == "content"

    def test_ingest_db_failure_does_not_break_error_path(self, tmp_path: Path) -> None:
        target = tmp_path / "bad.bin"
        target.write_bytes(b"\xff\x01 garbage")

        provider = FileProvider()
        db = MagicMock()
        db.record_provider_attempt.side_effect = RuntimeError("db down")
        with patch("obsidian_ai_tools.providers.file.get_db", return_value=db):
            with pytest.raises(UnicodeDecodeError):
                provider._ingest(str(target))
