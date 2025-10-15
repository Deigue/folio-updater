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


def add_column_to_table(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_type: str = "TEXT",
) -> bool:
    """Add a column to a table if it doesn't already exist.

    Args:
        connection: Database connection
        table_name: Name of the table to modify
        column_name: Name of the column to add
        column_type: SQL data type for the column (defaults to TEXT)

    Returns:
        True if column was added or already exists, False if there was an error
    """
    try:
        existing_columns = get_columns(connection, table_name)
        if column_name in existing_columns:  # pragma: no cover
            return True

        alter_sql = (
            f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {column_type}'
        )
        connection.execute(alter_sql)
        logger.debug("Added column '%s' to table '%s'", column_name, table_name)
    except sqlite3.OperationalError:
        logger.exception(
            "Could not add column '%s' to table '%s'",
            column_name,
            table_name,
        )
        return False
    else:
        return True


def update_rows(
    connection: sqlite3.Connection,
    table_name: str,
    updates: list[dict],
    where_columns: list[str],
    set_columns: list[str],
) -> int:
    """Update multiple rows in a table in batch.

    Args:
        connection: Database connection
        table_name: Name of the table to update
        updates: List of dicts containing the data for each update
        where_columns: List of column names to use in WHERE clause
        set_columns: List of column names to set in UPDATE clause

    Returns:
        Number of rows updated
    """
    if not updates:  # pragma: no cover
        return 0

    # Build the UPDATE query with placeholders
    set_clause = ", ".join(f'"{col}" = ?' for col in set_columns)
    where_clause = " AND ".join(f'"{col}" = ?' for col in where_columns)
    query = f'UPDATE "{table_name}" SET {set_clause} WHERE {where_clause}'

    try:
        params_list = []
        for update_data in updates:
            set_values = [update_data[col] for col in set_columns]
            where_values = [update_data[col] for col in where_columns]
            params_list.append(tuple(set_values + where_values))

        cursor = connection.executemany(query, params_list)
        connection.commit()
    except sqlite3.OperationalError:
        logger.exception("Error updating rows in table '%s'", table_name)
        return 0
    else:
        return cursor.rowcount
