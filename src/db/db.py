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
    db_path = get_config().db_path
    try:
        conn: sqlite3.Connection = sqlite3.connect(db_path)
    except sqlite3.OperationalError:
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


def get_rows(
    connection: sqlite3.Connection,
    table_name: str,
    which: str | None = None,
    n: int | None = None,
) -> pd.DataFrame:
    """Return rows from a table as a DataFrame.

    Optionally specify 'which' ('head' or 'tail') and 'n' (number of rows).
    """
    if n is not None and n <= 0:
        n = None
    if which == "head" and n is not None:
        query = f'SELECT * FROM "{table_name}" LIMIT {n}'
    elif which == "tail" and n is not None:
        query = f'SELECT * FROM "{table_name}" ORDER BY rowid DESC LIMIT {n}'
    else:
        query = f'SELECT * FROM "{table_name}"'
    try:
        df = pd.read_sql_query(query, connection)
    except pd.errors.DatabaseError:
        return pd.DataFrame()

    # For tail, reverse to preserve original order
    if which == "tail" and n is not None:
        return df.iloc[::-1].reset_index(drop=True)
    return df


def get_row_count(connection: sqlite3.Connection, table_name: str) -> int:
    """Return the number of rows in a table."""
    query = f'SELECT COUNT(*) FROM "{table_name}"'
    try:
        return connection.execute(query).fetchone()[0]
    except sqlite3.OperationalError:
        return 0
