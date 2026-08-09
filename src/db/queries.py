"""DB Helper functions."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pandas as pd

from app import get_config

if TYPE_CHECKING:
    from collections.abc import Generator


logger: logging.Logger = logging.getLogger(__name__)


@contextmanager
def get_connection() -> Generator[sqlite3.Connection]:
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
    where: str | None = None,
    params: list | tuple | None = None,
    order_by: str | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """Return rows from a table as a DataFrame with optional clauses.

    Args:
        connection: The database connection.
        table_name: The name of the table to query.
        where: An optional SQL WHERE clause (e.g., "id = ? AND name LIKE ?").
        params: An optional list or tuple of parameters to substitute into the
            `where` clause.
        order_by: An optional SQL ORDER BY clause (e.g., "name ASC").
        limit: An optional integer to limit the number of rows returned.

    Returns:
        A pandas DataFrame containing the query results.
    """
    query = f'SELECT * FROM "{table_name}"'
    if where:
        query += f" WHERE {where}"
    if order_by:
        query += f" ORDER BY {order_by}"
    if limit is not None:
        query += f" LIMIT {limit}"
    try:
        df = pd.read_sql_query(query, connection, params=params)
    except (pd.errors.DatabaseError, sqlite3.OperationalError):
        return pd.DataFrame()
    return df


def get_last_insert_rowid(connection: sqlite3.Connection) -> int:
    """Return the rowid of the most recent successful INSERT on this connection."""
    result = connection.execute("SELECT last_insert_rowid()").fetchone()
    return int(result[0])


def get_row_count(
    connection: sqlite3.Connection,
    table_name: str,
    where: str | None = None,
    params: list | tuple | None = None,
) -> int:
    """Return the number of rows in a table, optionally filtered.

    Args:
        connection: The database connection.
        table_name: The name of the table to query.
        where: An optional SQL WHERE clause.
        params: An optional list or tuple of parameters for the `where` clause.

    Returns:
        The number of rows matching the criteria.
    """
    query = f'SELECT COUNT(*) FROM "{table_name}"'
    if where:
        query += f" WHERE {where}"
    try:
        cursor = connection.execute(query, params or ())
        result = cursor.fetchone()
        return result[0] if result else 0
    except sqlite3.OperationalError:
        return 0


def get_max_value(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    condition: str | None = None,
) -> str | None:
    """Return the maximum value in a column, optionally filtered by a condition."""
    query = f'SELECT MAX("{column_name}") FROM "{table_name}"'
    if condition:
        query += f" WHERE {condition}"
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


def get_distinct_set(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    filter_condition: str | None = None,
) -> set[str]:
    """Return distinct non-null values from a column as a set."""
    df = get_distinct_values(connection, table_name, column_name, filter_condition)
    return set(df[column_name].dropna()) if column_name in df else set()


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


def insert_or_replace(
    connection: sqlite3.Connection,
    table_name: str,
    data: dict,
) -> bool:
    """Insert or replace a row in a table.

    Args:
        connection: Database connection
        table_name: Name of the table to insert/replace into
        data: Dictionary mapping column names to values

    Returns:
        True if successful, False if there was an error
    """
    if not data:  # pragma: no cover
        return False

    columns = ", ".join(f'"{col}"' for col in data)
    placeholders = ", ".join("?" for _ in data)
    query = f'INSERT OR REPLACE INTO "{table_name}" ({columns}) VALUES ({placeholders})'

    try:
        connection.execute(query, tuple(data.values()))
        connection.commit()
    except sqlite3.OperationalError:
        logger.exception(
            "Error inserting/replacing row in table '%s'",
            table_name,
        )
        return False
    else:
        return True


def delete_rows(
    connection: sqlite3.Connection,
    table_name: str,
    where: str | None = None,
    params: list | tuple | None = None,
) -> int:
    """Delete rows from a table based on WHERE conditions.

    Args:
        connection: Database connection
        table_name: Name of the table to delete from
        where: Optional SQL WHERE clause condition string
        params: An optional list or tuple of parameters for the `where` clause.

    Returns:
        Number of rows deleted
    """
    query = f'DELETE FROM "{table_name}"'
    if where:
        query += f" WHERE {where}"

    try:
        cursor = connection.execute(query, params or ())
        connection.commit()
    except sqlite3.OperationalError:
        logger.exception("Error deleting rows from table '%s'", table_name)
        return 0
    else:
        return cursor.rowcount

