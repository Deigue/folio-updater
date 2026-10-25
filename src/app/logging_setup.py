"""Logging configuration for the Folio project."""

from __future__ import annotations

import logging
import time
from logging.handlers import TimedRotatingFileHandler
from typing import TYPE_CHECKING

from config import Config

if TYPE_CHECKING:
    from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(module)s:%(lineno)4d %(message)s"
DATE_FORMAT = "%m-%d %H:%M:%S"


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


class SharedLogHandler(TimedRotatingFileHandler):
    """A midnight-rotating log file that several folio processes can share.

    Every process opens the same file, and Windows will not rename a file another
    process holds open, so the rollover fails whenever two commands overlap. The
    rotation is skipped then and tried again at the next midnight, instead of every
    later record printing a logging traceback.
    """

    def doRollover(self) -> None:  # noqa: N802
        """Rotate the file, or leave it be when another process holds it."""
        try:
            super().doRollover()
        except PermissionError:
            self.rolloverAt = self.computeRollover(int(time.time()))


def init_logging(level: int = logging.INFO) -> None:
    """Initialize logging for the application.

    Logging is set up with:
        - A rolling file log for the application (`folio.log`)
        - A separate rolling file log for import operations (`importer.log`)

    Args:
        level: The level the application logger records at.
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
        file_handler = SharedLogHandler(
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
        import_handler = SharedLogHandler(
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


def audit_footer() -> None:
    """Close an audit log entry with a separator line."""
    import_logger = get_import_logger()
    import_logger.info("=" * 80)
    import_logger.info("")
