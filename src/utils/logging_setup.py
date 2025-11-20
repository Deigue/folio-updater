"""Logging configuration for the Folio project."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
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
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(module)s:%(lineno)4d %(message)s"
DATE_FORMAT = "%m-%d %H:%M:%S"


def _supports_color() -> bool:  # pragma: no cover
    """Check if the terminal supports ANSI color codes.

    Returns:
        True if colors should be used, False otherwise
    """
    if sys.platform == "win32":
        # Windows Terminal, ConEmu, and other modern terminals set these
        if os.environ.get("WT_SESSION") or os.environ.get("ANSICON"):
            return True
        # Check if stdout is a terminal and supports colors
        if hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            # Try to enable ANSI support on Windows 10+
            try:
                kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                # Enable virtual terminal processing (0x0004)
                handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
                mode = ctypes.c_ulong()
                if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                    mode.value |= 0x0004
                    if kernel32.SetConsoleMode(handle, mode):
                        return True
            except (AttributeError, OSError):
                # If we can't enable ANSI, fall back to no colors
                pass
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


class CompactFormatter(logging.Formatter):
    """Formatter that enforces strict padding for module name and line number."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with strict module/line number padding."""
        module = record.module[:12].ljust(12)
        original_module = record.module
        record.module = module
        result = super().format(record)
        record.module = original_module
        return result


class ColorFormatter(CompactFormatter):  # pragma: no cover
    """Custom formatter to colorize log levels in console output."""

    def format(self, record: logging.LogRecord) -> str:
        """Colorize log levels in console output."""
        levelname_orig = record.levelname
        color: str = COLORS.get(record.levelno, RESET)
        record.levelname = f"{color}{record.levelname}{RESET}"
        output: str = super().format(record)
        record.levelname = levelname_orig
        return output


def init_logging(level: int = logging.INFO) -> None:
    """Intialize logging for the application.

    Logging is set up with:
        - Colorized console logs
        - Rolling file logs (general application logs)
        - Separate rolling file logs for import operations
    """
    log_dir: Path = Config.get_default_root_directory() / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file: Path = log_dir / "folio.log"

    root_logger: logging.Logger = logging.getLogger()
    root_logger.setLevel(level)

    # File handler (folio.log)
    file_exists = any(
        isinstance(h, TimedRotatingFileHandler) and h.baseFilename == str(log_file)
        for h in root_logger.handlers
    )
    if not file_exists:
        file_handler = TimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=14,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            CompactFormatter(LOG_FORMAT, datefmt=DATE_FORMAT),
        )
        root_logger.addHandler(file_handler)

    _setup_import_logger(log_dir, level)

    logger: logging.Logger = logging.getLogger(__name__)
    logger.debug("Logging initialized with level: %s", logging.getLevelName(level))


def _setup_import_logger(log_dir: Path, level: int) -> None:
    """Set up a dedicated logger for import operations.

    Args:
        log_dir: Directory where log files should be stored
        level: Logging level to use
    """
    import_log_file: Path = log_dir / "importer.log"
    import_logger: logging.Logger = logging.getLogger("importer")

    # Check if handler already exists
    import_handler_exists = any(
        isinstance(h, TimedRotatingFileHandler)
        and h.baseFilename == str(import_log_file)
        for h in import_logger.handlers
    )

    if not import_handler_exists:
        import_handler = TimedRotatingFileHandler(
            import_log_file,
            when="midnight",
            interval=1,
            backupCount=30,  # Keep more import logs for audit purposes
            encoding="utf-8",
        )
        import_handler.setFormatter(
            CompactFormatter(LOG_FORMAT, datefmt=DATE_FORMAT),
        )
        import_logger.addHandler(import_handler)
        import_logger.setLevel(level)
        import_logger.propagate = False


def get_import_logger() -> logging.Logger:
    """Get the dedicated import logger.

    Returns:
        Logger instance for import operations
    """
    return logging.getLogger("importer")
