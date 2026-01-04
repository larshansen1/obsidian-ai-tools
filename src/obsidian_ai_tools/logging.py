"""Logging configuration for obsidian-ai-tools."""

import logging
import logging.config
from pathlib import Path
from typing import Any

import structlog

# Default log file path
LOG_DIR = Path.home() / ".kai" / "logs"
LOG_FILE = LOG_DIR / "ingest.log"


def setup_logging(verbose: bool = False) -> None:
    """Configure structured logging.

    Args:
        verbose: If True, set log level to DEBUG, otherwise INFO.
    """
    # Ensure log directory exists
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Fallback to current directory if permission denied
        pass

    log_level = logging.DEBUG if verbose else logging.INFO

    # Configure processors
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    # Console renderer (pretty printing)
    console_processors = processors + [structlog.dev.ConsoleRenderer(colors=True)]

    # File renderer (JSON)
    file_processors = processors + [structlog.processors.JSONRenderer()]

    # Configure standard logging to capture library logs
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "plain": {
                    "()": structlog.stdlib.ProcessorFormatter,
                    "processors": console_processors,
                },
                "json": {
                    "()": structlog.stdlib.ProcessorFormatter,
                    "processors": file_processors,
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "plain",
                    "stream": "ext://sys.stderr",
                },
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "formatter": "json",
                    "filename": str(LOG_FILE),
                    "maxBytes": 10 * 1024 * 1024,  # 10 MB
                    "backupCount": 5,
                    "encoding": "utf-8",
                },
            },
            "loggers": {
                "": {  # Root logger
                    "handlers": ["file"],  # Only log to file by default to keep CLI clean
                    "level": log_level,
                    "propagate": True,
                },
                "obsidian_ai_tools": {
                    "handlers": ["file"],  # Only log structured events to file
                    "level": log_level,
                    "propagate": False,
                },
            },
        }
    )

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Generate and bind a unique trace ID for this execution
    import uuid

    structlog.contextvars.bind_contextvars(trace_id=str(uuid.uuid4()))
