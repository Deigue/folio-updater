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


def get_rows(  # noqa: PLR0913
    connection: sqlite3.Connection,
    table_name: str,
    which: str | None = None,
    n: int | None = None,
    condition: str | None = None,
    order_by: str | None = None,
) -> pd.DataFrame:  # pragma: no cover
    """Return rows from a table as a DataFrame.

    Optionally specify 'which' ('head' or 'tail'), 'n' (number of rows),
    'condition' (SQL WHERE clause), and 'order_by' (SQL ORDER BY clause).
    """
    if n is not None and n <= 0:
        n = None
    query = f'SELECT * FROM "{table_name}"'
    if condition:
        query += f" WHERE {condition}"
    if order_by:
        query += f" ORDER BY {order_by}"
    elif which == "tail" and n is not None:
        query += " ORDER BY rowid DESC"
    elif which == "head" and n is not None:
        pass  # no additional order
    if n is not None:
        query += f" LIMIT {n}"
    try:
        df = pd.read_sql_query(query, connection)
    except pd.errors.DatabaseError:
        return pd.DataFrame()

    # For tail, reverse to preserve original order
    if which == "tail" and n is not None:
        return df.iloc[::-1].reset_index(drop=True)
    return df


def get_row_count(
    connection: sqlite3.Connection,
    table_name: str,
    condition: str | None = None,
) -> int:
    """Return the number of rows in a table, optionally filtered by a condition."""
    query = f'SELECT COUNT(*) FROM "{table_name}"'
    if condition:
        query += f" WHERE {condition}"
    try:
        return connection.execute(query).fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def get_max_value(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
) -> str | None:
    """Return the maximum value in a column."""
    query = f'SELECT MAX("{column_name}") FROM "{table_name}"'
    try:
        result = connection.execute(query).fetchone()
        return result[0] if result and result[0] else None
    except sqlite3.OperationalError:
        return None


def get_min_value(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
) -> str | None:
    """Return the minimum value in a column."""
    query = f'SELECT MIN("{column_name}") FROM "{table_name}"'
    try:
        result = connection.execute(query).fetchone()
        return result[0] if result and result[0] else None
    except sqlite3.OperationalError:
        return None


def get_distinct_values(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    filter_condition: str | None = None,
    order_by: str | None = None,
) -> pd.DataFrame:
    """Return distinct values from a column with optional filtering and ordering."""
    query = f'SELECT DISTINCT "{column_name}" FROM "{table_name}"'

    if filter_condition:
        query += f" WHERE {filter_condition}"

    if order_by:
        query += f" ORDER BY {order_by}"

    try:
        return pd.read_sql_query(query, connection)
    except (sqlite3.OperationalError, pd.errors.DatabaseError):
        return pd.DataFrame()


def drop_table(connection: sqlite3.Connection, table_name: str) -> None:
    """Drop a table if it exists."""
    query = f'DROP TABLE IF EXISTS "{table_name}"'
    try:
        connection.execute(query)
        connection.commit()
    except sqlite3.OperationalError:
        logger.exception("Error dropping table: %s", table_name)
