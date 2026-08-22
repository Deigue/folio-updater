"""DB Helper functions."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pandas as pd

from app import get_config
from utils.constants import Column, Table

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


def _as_float(value: str) -> float | None:
    """Parse a filter value as a number, or None when it is not one."""
    try:
        return float(value)
    except ValueError:
        return None


def build_comparison_clause(
    column: str,
    operator: str,
    value: str,
    *,
    text_numeric: bool,
) -> tuple[str, str | float]:
    """Build one comparison clause and the parameter to bind to it.

    Args:
        column: The column being compared.
        operator: A SQL comparison operator, such as `=` or `<=`.
        value: The raw filter value.
        text_numeric: Whether the column holds numbers as text.

    Returns:
        The clause, and the parameter to bind to its placeholder.
    """
    if text_numeric:
        number = _as_float(value)
        if number is not None:
            return f'CAST("{column}" AS REAL) {operator} ?', number
    return f'"{column}" {operator} ?', value


def build_sort_clause(column: str, direction: str, *, text_numeric: bool) -> str:
    """Build one ORDER BY term.

    Args:
        column: The column to sort on.
        direction: `ASC` or `DESC`.
        text_numeric: Whether the column holds numbers as text.

    Returns:
        The ORDER BY term.
    """
    operand = f'CAST("{column}" AS REAL)' if text_numeric else f'"{column}"'
    return f"{operand} {direction}"


def get_rows(  # noqa: PLR0913
    connection: sqlite3.Connection,
    table_name: str,
    *,
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


def get_alias_edges(
    connection: sqlite3.Connection,
) -> list[tuple[str, str, str]]:
    """Return every ticker rename as an (old, new, effective date) triple.

    Args:
        connection: The database connection.

    Returns:
        One triple per row of `TickerAliases`, ordered by effective date so
        multi-hop chains resolve deterministically. Empty when the table has
        not been created yet.
    """
    query = (
        f'SELECT "{Column.Aliases.OLD_TICKER}", "{Column.Aliases.NEW_TICKER}", '
        f'"{Column.Aliases.EFFECTIVE_DATE}" FROM "{Table.TICKER_ALIASES}" '
        f'ORDER BY "{Column.Aliases.EFFECTIVE_DATE}" ASC'
    )
    try:
        rows = connection.execute(query).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        (str(old).upper(), str(new).upper(), str(effective))
        for old, new, effective in rows
        if old and new
    ]


def get_txns_fingerprint(connection: sqlite3.Connection) -> str:
    """Summarise the transactions table cheaply enough to run on every command.

    Any add, edit, delete or import moves at least one of these aggregates, so
    comparing the result against a cached one is a sound staleness check without
    reading the rows themselves.

    Args:
        connection: The database connection.

    Returns:
        A pipe-joined digest of row count, max TxnId, summed Amount and Units,
        and max TxnDate. `"empty"` when the table does not exist.
    """
    query = (
        f'SELECT COUNT(*), MAX("{Column.Txn.TXN_ID}"), '
        f'TOTAL("{Column.Txn.AMOUNT}"), TOTAL("{Column.Txn.UNITS}"), '
        f'MAX("{Column.Txn.TXN_DATE}") FROM "{Table.TXNS}"'
    )
    try:
        result = connection.execute(query).fetchone()
    except sqlite3.OperationalError:
        return "empty"
    return "|".join("" if value is None else str(value) for value in result)


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


def insert_or_replace_many(
    connection: sqlite3.Connection,
    table_name: str,
    rows: list[dict],
) -> int:
    """Insert or replace many rows in one statement.

    The batched form of `insert_or_replace`. Every row must carry the same
    columns.

    Args:
        connection: Database connection
        table_name: Name of the table to insert/replace into
        rows: Rows as dictionaries mapping column names to values. First row
            will determine column names.

    Returns:
        Number of rows written, or 0 if there was an error.
    """
    if not rows:
        return 0

    keys = list(rows[0])
    columns = ", ".join(f'"{col}"' for col in keys)
    placeholders = ", ".join("?" for _ in keys)
    query = f'INSERT OR REPLACE INTO "{table_name}" ({columns}) VALUES ({placeholders})'

    try:
        params_list = [tuple(row.get(col) for col in keys) for row in rows]
        cursor = connection.executemany(query, params_list)
        connection.commit()
    except sqlite3.OperationalError:
        logger.exception(
            "Error inserting/replacing rows in table '%s'",
            table_name,
        )
        return 0
    else:
        return cursor.rowcount


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
