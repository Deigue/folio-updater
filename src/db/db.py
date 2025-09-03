"""DB Helper functions."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from typing import Generator

import pandas as pd

from app.app_context import get_config

logger: logging.Logger = logging.getLogger(__name__)


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    """Return sqlite3.Connection. Ensure parent data folder exists."""
    try:
        db_path = get_config().db_path
        conn: sqlite3.Connection = sqlite3.connect(db_path)
    except sqlite3.OperationalError:  # pragma: no cover
        logger.exception("Error connecting to database: %s", str(db_path))
        raise
    try:
        yield conn
    finally:
        conn.close()


def get_columns(connection: sqlite3.Connection, table_name: str) -> list[str]:
    """Return list of column names for a table (in defined order)."""
    cursor = connection.execute(f"PRAGMA table_info('{table_name}')")
    return [row[1] for row in cursor.fetchall()]


def get_tables(connection: sqlite3.Connection) -> list[str]:
    """Return list of table names in the database."""
    cursor = connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return [row[0] for row in cursor.fetchall()]


def get_rows(connection: sqlite3.Connection, table_name: str) -> pd.DataFrame:
    """Return all rows from a table as a DataFrame."""
    query = f'SELECT * FROM "{table_name}"'  # noqa: S608
    return pd.read_sql_query(query, connection)
