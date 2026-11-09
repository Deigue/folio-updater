"""DB Helper functions."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pandas as pd

from db.catalog import table_exists
from domain import Column, Table

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


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


def build_in_clause(column: str, values: Sequence[object] | None) -> str | None:
    """Build a clause matching any of `values`, or None to match every row."""
    if values is None:
        return None
    return f'"{column}" IN ({", ".join("?" for _ in values)})'


def get_rows(  # noqa: PLR0913
    connection: sqlite3.Connection,
    table_name: str,
    *,
    columns: Sequence[str] | None = None,
    where: str | None = None,
    params: list | tuple | None = None,
    order_by: str | None = None,
) -> pd.DataFrame:
    """Return rows from a table as a DataFrame with optional clauses.

    Args:
        connection: The database connection.
        table_name: The name of the table to query.
        columns: The columns to read, every one when omitted.
        where: An optional SQL WHERE clause (e.g., "id = ? AND name LIKE ?").
        params: An optional list or tuple of parameters to substitute into the
            `where` clause.
        order_by: An optional SQL ORDER BY clause (e.g., "name ASC").

    Returns:
        A pandas DataFrame containing the query results, empty without a table.
    """
    if not table_exists(connection, table_name):
        return pd.DataFrame()
    selected = ", ".join(f'"{col}"' for col in columns) if columns else "*"
    query = f'SELECT {selected} FROM "{table_name}"'
    if where:
        query += f" WHERE {where}"
    if order_by:
        query += f" ORDER BY {order_by}"
    try:
        return pd.read_sql_query(query, connection, params=params)
    except pd.errors.DatabaseError as error:
        # Pandas' own error is not one callers of this package catch.
        raise sqlite3.DatabaseError(str(error)) from error


def get_records(  # noqa: PLR0913
    connection: sqlite3.Connection,
    table_name: str,
    *,
    where: str | None = None,
    params: Sequence[object] | None = None,
    order_by: str | None = None,
    limit: int | None = None,
) -> list[dict[str, object]]:
    """Return rows exactly as stored, keyed by column name; none without a table."""
    if not table_exists(connection, table_name):
        return []
    query = f'SELECT * FROM "{table_name}"'
    if where:
        query += f" WHERE {where}"
    if order_by:
        query += f" ORDER BY {order_by}"
    if limit is not None:
        query += f" LIMIT {limit}"
    cursor = connection.execute(query, params or ())
    names = [description[0] for description in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor]


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
        The number of rows matching the criteria, 0 without a table.
    """
    if not table_exists(connection, table_name):
        return 0
    query = f'SELECT COUNT(*) FROM "{table_name}"'
    if where:
        query += f" WHERE {where}"
    (count,) = connection.execute(query, params or ()).fetchone()
    return count


def get_max_value(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
) -> str | None:
    """Return the maximum value in a column, or None without a table or a value."""
    if not table_exists(connection, table_name):
        return None
    query = f'SELECT MAX("{column_name}") FROM "{table_name}"'
    result = connection.execute(query).fetchone()
    return result[0] if result and result[0] else None


def earliest_transaction_date(connection: sqlite3.Connection) -> str | None:
    """Return the earliest transaction date among all stored transactions.

    Args:
        connection: The database connection.

    Returns:
        The date as YYYY-MM-DD, or None when the folio has no transactions.
    """
    return get_min_value(connection, Table.TXNS, Column.Txn.TXN_DATE)


def get_broker_latest_txn_date(
    connection: sqlite3.Connection,
    broker: str,
) -> str | None:
    """Return the latest transaction date among the accounts whose name holds `broker`.

    Args:
        connection: The database connection.
        broker: Broker key as it appears in account names, in any case.

    Returns:
        The date as YYYY-MM-DD, or None when the broker has no transactions.
    """
    if not table_exists(connection, Table.TXNS):
        return None
    query = (
        f'SELECT MAX("{Column.Txn.TXN_DATE}") FROM "{Table.TXNS}" '
        f'WHERE UPPER("{Column.Txn.ACCOUNT}") LIKE UPPER(?)'
    )
    result = connection.execute(query, (f"%{broker}%",)).fetchone()
    return result[0] if result and result[0] else None


def get_min_value(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
) -> str | None:
    """Return the minimum value in a column, or None without a table or a value."""
    if not table_exists(connection, table_name):
        return None
    query = f'SELECT MIN("{column_name}") FROM "{table_name}"'
    result = connection.execute(query).fetchone()
    return result[0] if result and result[0] else None


def get_distinct_values(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    filter_condition: str | None = None,
    order_by: str | None = None,
) -> list[object]:
    """Return a column's distinct values, with optional filtering and ordering."""
    if not table_exists(connection, table_name):
        return []
    query = f'SELECT DISTINCT "{column_name}" FROM "{table_name}"'
    if filter_condition:
        query += f" WHERE {filter_condition}"
    if order_by:
        query += f" ORDER BY {order_by}"
    return [row[0] for row in connection.execute(query)]


def get_distinct_set(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    filter_condition: str | None = None,
) -> set[str]:
    """Return distinct non-null values from a column as a set."""
    values = get_distinct_values(connection, table_name, column_name, filter_condition)
    return {str(value) for value in values if value is not None}


def get_fx_summary(connection: sqlite3.Connection, cutoff: str | None) -> str:
    """Summarise the FX rates a conversion dated on or before `cutoff` can use.

    A conversion takes the latest rate on or before its date, or the earliest
    rate held when its date precedes them all. Rates dated after the cutoff
    therefore change no conversion and are left out, except the earliest one.

    Args:
        connection: The database connection.
        cutoff: The latest date anything converts at, in YYYY-MM-DD form, or
            None when nothing converts.

    Returns:
        A pipe-joined digest of the count, summed USDCAD rate and latest date
        of those rates. `"empty"` when the table does not exist.
    """
    if not table_exists(connection, Table.FX):
        return "empty"
    query = (
        f'SELECT COUNT(*), TOTAL("{Column.FX.FXUSDCAD}"), MAX("{Column.FX.DATE}") '
        f'FROM "{Table.FX}" WHERE "{Column.FX.DATE}" <= ? OR "{Column.FX.DATE}" = '
        f'(SELECT MIN("{Column.FX.DATE}") FROM "{Table.FX}")'
    )
    result = connection.execute(query, (cutoff,)).fetchone()
    return "|".join("" if value is None else str(value) for value in result)


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

    params_list = []
    for update_data in updates:
        set_values = [update_data[col] for col in set_columns]
        where_values = [update_data[col] for col in where_columns]
        params_list.append(tuple(set_values + where_values))

    with connection:
        cursor = connection.executemany(query, params_list)
    return cursor.rowcount


def _insert_sql(
    table_name: str,
    columns: Sequence[str],
    *,
    replace: bool = False,
) -> str:
    """Render the one INSERT every write shares, one placeholder per column."""
    verb = "INSERT OR REPLACE" if replace else "INSERT"
    listed = ", ".join(f'"{column}"' for column in columns)
    marks = ", ".join("?" for _ in columns)
    return f'{verb} INTO "{table_name}" ({listed}) VALUES ({marks})'


def insert_or_replace_many(
    connection: sqlite3.Connection,
    table_name: str,
    rows: list[dict],
) -> int:
    """Insert or replace many rows, all or none.

    Every row must carry the same columns.

    Args:
        connection: Database connection
        table_name: Name of the table to insert/replace into
        rows: Rows as dictionaries mapping column names to values. First row
            will determine column names.

    Returns:
        Number of rows written.
    """
    if not rows:
        return 0

    keys = list(rows[0])
    query = _insert_sql(table_name, keys, replace=True)
    params_list = [tuple(row.get(col) for col in keys) for row in rows]
    with connection:
        cursor = connection.executemany(query, params_list)
    return cursor.rowcount


def begin_write(connection: sqlite3.Connection) -> None:
    """Open a transaction now, so a schema change rolls back with the rows after it."""
    connection.execute("BEGIN IMMEDIATE")


def insert_rows(
    connection: sqlite3.Connection,
    table_name: str,
    rows: Sequence[Mapping[str, object]],
) -> list[int]:
    """Insert rows all or none, refusing any that clash, returning each one's rowid."""
    with connection:
        return [_insert(connection, table_name, row) for row in rows]


def _insert(
    connection: sqlite3.Connection,
    table_name: str,
    row: Mapping[str, object],
) -> int:
    """Insert one row without committing, returning its rowid."""
    query = _insert_sql(table_name, list(row))
    return int(connection.execute(query, tuple(row.values())).lastrowid)  # ty: ignore[invalid-argument-type]


def trial_insert_rows(
    connection: sqlite3.Connection,
    table_name: str,
    rows: Sequence[Mapping[str, object]],
) -> list[sqlite3.IntegrityError | None]:
    """Try rows one at a time, then undo every one of them.

    Args:
        connection: Database connection
        table_name: Name of the table to try the rows against
        rows: The rows, keyed by column name.

    Returns:
        One entry per row tried: None for a row that went in, the error for one
        the table refused. It stops at the first refusal, so that is the last
        entry.
    """
    outcomes: list[sqlite3.IntegrityError | None] = []
    connection.execute("SAVEPOINT trial")
    try:
        for row in rows:
            try:
                _insert(connection, table_name, row)
            except sqlite3.IntegrityError as error:
                outcomes.append(error)
                break
            outcomes.append(None)
    finally:
        connection.execute("ROLLBACK TO trial")
        connection.execute("RELEASE trial")
    return outcomes


def replace_rows(
    connection: sqlite3.Connection,
    table_name: str,
    rows: list[dict],
    where: str,
    params: list | tuple,
) -> int:
    """Swap the rows matching a condition for new ones, in one transaction.

    Either every matching row is replaced or, on an error, nothing changes.

    Args:
        connection: Database connection
        table_name: Name of the table to rewrite part of
        rows: The new rows, at least one, every one carrying the same columns.
        where: SQL WHERE clause choosing the rows being replaced.
        params: Parameters for the `where` clause.

    Returns:
        Number of rows written.
    """
    keys = list(rows[0])
    insert = _insert_sql(table_name, keys)
    with connection:
        connection.execute(f'DELETE FROM "{table_name}" WHERE {where}', params)
        connection.executemany(
            insert,
            [tuple(row.get(col) for col in keys) for row in rows],
        )
    return len(rows)


def delete_rows(
    connection: sqlite3.Connection,
    table_name: str,
    where: str | None = None,
    params: Sequence[object] | None = None,
) -> int:
    """Delete rows from a table based on WHERE conditions.

    Args:
        connection: Database connection
        table_name: Name of the table to delete from
        where: Optional SQL WHERE clause condition string
        params: An optional list or tuple of parameters for the `where` clause.

    Returns:
        Number of rows deleted, 0 without a table.
    """
    if not table_exists(connection, table_name):
        return 0
    query = f'DELETE FROM "{table_name}"'
    if where:
        query += f" WHERE {where}"

    with connection:
        cursor = connection.execute(query, params or ())
    return cursor.rowcount
