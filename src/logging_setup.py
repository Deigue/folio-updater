"""Logging configuration for the Folio project."""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from typing import TYPE_CHECKING

from src.config import Config

if TYPE_CHECKING:
    from pathlib import Path

COLORS: dict[int, str] = {
    logging.DEBUG: "\033[36m",  # Cyan
    logging.INFO: "\033[32m",  # Green
    logging.WARNING: "\033[33m",  # Yellow
    logging.ERROR: "\033[31m",  # Red
    logging.CRITICAL: "\033[41m",  # Red background
}

RESET: str = "\033[0m"


class ColorFormatter(logging.Formatter):
    """Custom formatter to colorize log levels in console output."""

    def format(self, record: logging.LogRecord) -> str:
        """Colorize log levels in console output."""
        # Store original level name for file logs
        levelname_orig = record.levelname
        color: str = COLORS.get(record.levelno, RESET)
        record.levelname = f"{color}{record.levelname}{RESET}"

        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        output: str = formatter.format(record)

        # Restore original so file logs aren't colorized
        record.levelname = levelname_orig
        return output


def init_logging(level: int = logging.INFO) -> None:
    """Intialize logging for the application.

    Logging is set up with:
        - Colorized console logs
        - Rolling file logs
    """
    log_dir: Path = Config.get_project_root() / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file: Path = log_dir / "folio.log"

    root_logger: logging.Logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplication in notebooks
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console handler (colorized)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(ColorFormatter())
    root_logger.addHandler(console_handler)

    # File handler
    file_handler = TimedRotatingFileHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=14,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ),
    )
    root_logger.addHandler(file_handler)
    logger: logging.Logger = logging.getLogger(__name__)
    logger.debug("Logging initialized with level: %s", logging.getLevelName(level))
