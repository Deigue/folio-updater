"""Logging configuration for the Folio project."""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from typing import TYPE_CHECKING

from utils.config import Config

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
LOG_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s(%(lineno)d)] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class ColorFormatter(logging.Formatter):
    """Custom formatter to colorize log levels in console output."""

    def format(self, record: logging.LogRecord) -> str:
        """Colorize log levels in console output."""
        # Store original level name for file logs
        levelname_orig = record.levelname
        color: str = COLORS.get(record.levelno, RESET)
        record.levelname = f"{color}{record.levelname}{RESET}"

        formatter = logging.Formatter(
            LOG_FORMAT,
            datefmt=DATE_FORMAT,
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
    log_dir: Path = Config.get_default_root_directory() / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file: Path = log_dir / "folio.log"

    root_logger: logging.Logger = logging.getLogger()
    root_logger.setLevel(level)

    # Console handler (colorized)
    console_exists = any(
        isinstance(h, logging.StreamHandler) and isinstance(h.formatter, ColorFormatter)
        for h in root_logger.handlers
    )
    if not console_exists:  # pragma: no cover
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(ColorFormatter())
        root_logger.addHandler(console_handler)

    # File handler
    file_exists = any(
        isinstance(h, TimedRotatingFileHandler) and h.baseFilename == str(log_file)
        for h in root_logger.handlers
    )
    if not file_exists:  # pragma: no cover
        file_handler = TimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=14,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter(
                LOG_FORMAT,
                datefmt=DATE_FORMAT,
            ),
        )
        root_logger.addHandler(file_handler)

    logger: logging.Logger = logging.getLogger(__name__)
    logger.debug("Logging initialized with level: %s", logging.getLevelName(level))
