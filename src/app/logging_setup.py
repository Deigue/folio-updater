"""Logging configuration for the Folio project."""

from __future__ import annotations

import logging
import time
from logging.handlers import TimedRotatingFileHandler
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(module)s:%(lineno)4d %(message)s"
DATE_FORMAT = "%m-%d %H:%M:%S"
AUDIT_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"
AUDIT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
AUDIT_LOGGER = "audit"


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


def init_logging(
    log_dir: Path,
    level: int = logging.INFO,
    audit_level: int = logging.INFO,
) -> None:
    """Initialize logging for the application.

    Logging is set up with:
        - A rolling file log for the application (`folio.log`)
        - A separate rolling file log for the audit trail (`audit.log`)

    Args:
        log_dir: The folder both logs are written into.
        level: The level the application logger records at.
        audit_level: The level the audit logger records at.
    """
    log_dir.mkdir(exist_ok=True)
    log_file: Path = log_dir / "folio.log"

    root_logger: logging.Logger = logging.getLogger()
    root_logger.setLevel(level)
    _add_file_handler(
        root_logger,
        log_file,
        backup_count=14,
        formatter=CompactFormatter(LOG_FORMAT, datefmt=DATE_FORMAT),
    )

    audit_logger = get_audit_logger()
    # Keep 30 days of the audit trail, against folio.log's 14.
    _add_file_handler(
        audit_logger,
        log_dir / "audit.log",
        backup_count=30,
        formatter=logging.Formatter(AUDIT_FORMAT, datefmt=AUDIT_DATE_FORMAT),
    )
    audit_logger.setLevel(audit_level)
    audit_logger.propagate = False

    logger: logging.Logger = logging.getLogger(__name__)
    logger.debug("Logging initialized with level: %s", logging.getLevelName(level))


def _add_file_handler(
    logger: logging.Logger,
    log_file: Path,
    backup_count: int,
    formatter: logging.Formatter,
) -> None:
    """Give a logger its rolling file, unless an earlier setup already did."""
    if any(
        isinstance(h, TimedRotatingFileHandler) and h.baseFilename == str(log_file)
        for h in logger.handlers
    ):
        return
    handler = SharedLogHandler(
        log_file,
        when="midnight",
        interval=1,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def get_audit_logger() -> logging.Logger:
    """Get the logger that writes the audit trail.

    Returns:
        The audit logger.
    """
    return logging.getLogger(AUDIT_LOGGER)


def audit_footer() -> None:
    """Close an audit log entry with a separator line."""
    audit_logger = get_audit_logger()
    audit_logger.info("=" * 80)
    audit_logger.info("")
