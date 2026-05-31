"""Tests for logging configuration."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from obsidian_ai_tools.logging import setup_logging


def test_setup_logging_uses_temp_directory_when_home_log_is_unwritable(tmp_path: Path) -> None:
    """Test logging falls back to a writable temporary directory."""
    unwritable_log_dir = MagicMock()
    unwritable_log_dir.mkdir.side_effect = OSError("permission denied")

    with (
        patch("obsidian_ai_tools.logging.LOG_DIR", unwritable_log_dir),
        patch("obsidian_ai_tools.logging.tempfile.gettempdir", return_value=str(tmp_path)),
        patch("obsidian_ai_tools.logging.logging.config.dictConfig") as mock_dict_config,
        patch("obsidian_ai_tools.logging.structlog.configure"),
        patch("obsidian_ai_tools.logging.structlog.contextvars.bind_contextvars"),
    ):
        setup_logging()

    config = mock_dict_config.call_args.args[0]
    assert config["handlers"]["file"]["filename"] == str(tmp_path / "kai" / "logs" / "ingest.log")
