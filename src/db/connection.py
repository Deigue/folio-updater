"""Opening the folio's database, migrated to the latest schema."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING

from app import get_config
from db.migrations import bring_up_to_date

if TYPE_CHECKING:
    from collections.abc import Generator

logger: logging.Logger = logging.getLogger(__name__)


@contextmanager
def get_connection() -> Generator[sqlite3.Connection]:
    """Open the folio's SQLite file, migrated to the latest schema."""
    db_path = get_config().db_path
    try:
        conn: sqlite3.Connection = sqlite3.connect(db_path)
    except sqlite3.OperationalError:
        logger.exception("Error connecting to database: %s", str(db_path))
        raise
    try:
        bring_up_to_date(conn)
        yield conn
    finally:
        conn.close()
