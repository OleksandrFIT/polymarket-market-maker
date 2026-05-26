"""Structured logging setup.

JSON output to file (machine-parseable, for ``jq``-based analysis post-hoc),
human-readable to stderr (for live tail).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import structlog


def setup_logging(log_path: str, level: str = "INFO") -> None:
    """Configure structlog + stdlib logging.

    Idempotent — safe to call multiple times.
    """
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    # Clear existing handlers (idempotent)
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    # File handler — raw JSON one event per line
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(message)s"))
    file_handler.setLevel(logging.DEBUG)

    # stderr handler — colored for humans
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(logging.Formatter("%(message)s"))
    stderr_handler.setLevel(getattr(logging, level.upper(), logging.INFO))

    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(stderr_handler)

    # Tame noisy libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    """Return a bound structlog logger. Name appears in 'logger' field."""
    return structlog.get_logger(name)
