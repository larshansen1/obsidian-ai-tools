"""Tests for logging configuration."""

import logging
import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import structlog

from obsidian_ai_tools.logging import setup_logging

HOME_LOG_DIR = Path(".kai") / "logs"
FALLBACK_LOG_DIR = Path("kai") / "logs"


def _call_setup_logging(
    *,
    home_log_dir: Path,
    tempdir: str,
    verbose: bool | None = None,
) -> tuple[dict[str, Any], MagicMock, MagicMock, MagicMock]:
    """Run setup_logging with global side effects mocked.

    LOG_DIR is a real path under ``home_log_dir`` so the mkdir/open calls run
    for real (killing argument-mutating mutants), while dictConfig /
    structlog.configure / bind_contextvars are captured for assertions.

    Returns:
        The dictConfig config dict plus the three mocks.
    """
    stack = ExitStack()
    stack.enter_context(patch("obsidian_ai_tools.logging.LOG_DIR", home_log_dir))
    stack.enter_context(
        patch(
            "obsidian_ai_tools.logging.LOG_FILE",
            home_log_dir / HOME_LOG_DIR / "ingest.log",
        )
    )
    stack.enter_context(
        patch("obsidian_ai_tools.logging.tempfile.gettempdir", return_value=tempdir)
    )
    mock_dict = stack.enter_context(patch("obsidian_ai_tools.logging.logging.config.dictConfig"))
    mock_struct = stack.enter_context(patch("obsidian_ai_tools.logging.structlog.configure"))
    mock_bind = stack.enter_context(
        patch("obsidian_ai_tools.logging.structlog.contextvars.bind_contextvars")
    )
    mock_open = stack.enter_context(patch.object(Path, "open"))
    try:
        if verbose is None:
            setup_logging()  # exercise the default parameter value
        else:
            setup_logging(verbose=verbose)
    finally:
        stack.close()
    return mock_dict.call_args.args[0], mock_struct, mock_bind, mock_open


def _assert_shared_config(config: dict[str, Any], level: int, expected_file: str) -> None:
    """Assert the full dictConfig payload structure setup_logging emits."""
    assert config["version"] == 1
    assert config["disable_existing_loggers"] is False
    assert set(config["formatters"]) == {"plain", "json"}
    assert set(config["handlers"]) == {"console", "file"}

    plain = config["formatters"]["plain"]
    json_fmt = config["formatters"]["json"]
    assert plain["()"] is structlog.stdlib.ProcessorFormatter
    assert json_fmt["()"] is structlog.stdlib.ProcessorFormatter

    for processors in (plain["processors"], json_fmt["processors"]):
        timestamps = [p for p in processors if isinstance(p, structlog.processors.TimeStamper)]
        assert timestamps and timestamps[0].fmt == "iso"
    console = [p for p in plain["processors"] if isinstance(p, structlog.dev.ConsoleRenderer)]
    assert console and console[0].colors is True
    json_renderers = [
        p for p in json_fmt["processors"] if isinstance(p, structlog.processors.JSONRenderer)
    ]
    assert len(json_renderers) == 1

    console_handler = config["handlers"]["console"]
    assert console_handler["class"] == "logging.StreamHandler"
    assert console_handler["stream"] == "ext://sys.stderr"

    file_handler = config["handlers"]["file"]
    assert file_handler["class"] == "logging.handlers.RotatingFileHandler"
    assert file_handler["filename"] == expected_file
    assert file_handler["maxBytes"] == 10 * 1024 * 1024
    assert file_handler["backupCount"] == 5
    assert file_handler["encoding"] == "utf-8"

    assert set(config["loggers"]) == {"", "obsidian_ai_tools"}
    root = config["loggers"][""]
    assert root["handlers"] == ["file"]
    assert root["level"] == level
    assert root["propagate"] is True
    module_logger = config["loggers"]["obsidian_ai_tools"]
    assert module_logger["handlers"] == ["file"]
    assert module_logger["level"] == level
    assert module_logger["propagate"] is False


def _assert_structlog_config(mock_struct: MagicMock, mock_bind: MagicMock) -> None:
    """Assert the structlog.configure call captures every processor and flag."""
    kwargs = mock_struct.call_args.kwargs
    processors = kwargs["processors"]
    assert isinstance(processors, list)
    assert len(processors) == 8
    assert processors[0] is structlog.stdlib.filter_by_level
    assert processors[1] is structlog.stdlib.add_logger_name
    assert processors[2] is structlog.stdlib.add_log_level
    assert isinstance(processors[3], structlog.stdlib.PositionalArgumentsFormatter)
    timestamps = [p for p in processors if isinstance(p, structlog.processors.TimeStamper)]
    assert timestamps and timestamps[0].fmt == "iso"
    assert processors[-1] is structlog.stdlib.ProcessorFormatter.wrap_for_formatter
    assert kwargs["context_class"] is dict
    assert isinstance(kwargs["logger_factory"], structlog.stdlib.LoggerFactory)
    assert kwargs["wrapper_class"] is structlog.stdlib.BoundLogger
    assert kwargs["cache_logger_on_first_use"] is True

    trace_id = mock_bind.call_args.kwargs["trace_id"]
    assert isinstance(trace_id, str)
    assert len(trace_id) == 36
    assert uuid.UUID(trace_id)  # raises for non-UUID trace ids


def _assert_file_open(mock_open: MagicMock) -> None:
    """Assert the home log file is opened for append with UTF-8 encoding."""
    call = mock_open.call_args
    assert call.kwargs.get("encoding") == "utf-8"
    # Path.open(mode="a", ...) passes mode positionally
    assert call.args and call.args[0] == "a"


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
    assert config["handlers"]["file"]["filename"] == str(tmp_path / FALLBACK_LOG_DIR / "ingest.log")


def test_setup_logging_defaults_to_info_level_and_full_config(tmp_path: Path) -> None:
    """setup_logging() without verbose produces INFO level and the full config."""
    home = tmp_path / "deep" / "home"
    config, mock_struct, mock_bind, mock_open = _call_setup_logging(
        home_log_dir=home, tempdir=str(tmp_path / "temp")
    )

    _assert_shared_config(config, logging.INFO, str(home / HOME_LOG_DIR / "ingest.log"))
    _assert_structlog_config(mock_struct, mock_bind)
    _assert_file_open(mock_open)


def test_setup_logging_verbose_sets_debug_level(tmp_path: Path) -> None:
    """setup_logging(verbose=True) produces DEBUG level everywhere."""
    home = tmp_path / "deep" / "home"
    config, _, _, _ = _call_setup_logging(
        home_log_dir=home, tempdir=str(tmp_path / "temp"), verbose=True
    )
    _assert_shared_config(config, logging.DEBUG, str(home / HOME_LOG_DIR / "ingest.log"))


def test_setup_logging_reuses_existing_home_log_dir(tmp_path: Path) -> None:
    """An already-created log directory is reused (exist_ok=True)."""
    home = tmp_path / "home"
    (home / HOME_LOG_DIR).mkdir(parents=True)
    config, _, _, _ = _call_setup_logging(home_log_dir=home, tempdir=str(tmp_path / "temp"))
    assert config["handlers"]["file"]["filename"] == str(home / HOME_LOG_DIR / "ingest.log")


def test_setup_logging_fallback_reuses_precreated_dir(tmp_path: Path) -> None:
    """Fallback mkdir tolerates an already-existing directory."""
    unwritable_log_dir = MagicMock()
    unwritable_log_dir.mkdir.side_effect = OSError("permission denied")
    (tmp_path / FALLBACK_LOG_DIR).mkdir(parents=True)

    with (
        patch("obsidian_ai_tools.logging.LOG_DIR", unwritable_log_dir),
        patch("obsidian_ai_tools.logging.tempfile.gettempdir", return_value=str(tmp_path)),
        patch("obsidian_ai_tools.logging.logging.config.dictConfig") as mock_dict_config,
        patch("obsidian_ai_tools.logging.structlog.configure"),
        patch("obsidian_ai_tools.logging.structlog.contextvars.bind_contextvars"),
    ):
        setup_logging()

    config = mock_dict_config.call_args.args[0]
    assert config["handlers"]["file"]["filename"] == str(tmp_path / FALLBACK_LOG_DIR / "ingest.log")
