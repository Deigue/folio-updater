"""Schema reads and changes on a connection the caller opened."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from db.ddl import (
    EXTRA_COLUMN_TYPE,
    WATCHED_TABLES,
    change_token,
    create_revisions,
    create_table,
    watch_triggers,
)
from domain import Column, Table

if TYPE_CHECKING:
    import sqlite3

logger = logging.getLogger(__name__)


def get_columns(connection: sqlite3.Connection, table_name: str) -> list[str]:
    """Return list of column names for a table (in defined order)."""
    cursor = connection.execute(f"PRAGMA table_info('{table_name}')")
    return [row[1] for row in cursor.fetchall()]


def get_tables(connection: sqlite3.Connection) -> list[str]:
    """Return list of table names in the database."""
    return _schema_names(connection, "table")


def table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    """Whether a table exists; SQLite's own error cannot tell this from any other."""
    return table_name in get_tables(connection)


def get_triggers(connection: sqlite3.Connection) -> list[str]:
    """Return list of trigger names in the database."""
    return _schema_names(connection, "trigger")


def get_revisions(connection: sqlite3.Connection) -> dict[str, str]:
    """Return the change token of every watched table that exists.

    A dropped table keeps its row in `Revisions`, so the join leaves it out:
    a folio whose transactions were dropped must not match a ledger built from
    them.

    Args:
        connection: The database connection.

    Returns:
        Table name to token. Empty when no table has a token yet.
    """
    if not table_exists(connection, Table.REVISIONS):
        return {}
    query = (
        f'SELECT "{Column.Revision.TABLE_NAME}", "{Column.Revision.TOKEN}" '
        f'FROM "{Table.REVISIONS}" WHERE "{Column.Revision.TABLE_NAME}" IN '
        "(SELECT name FROM sqlite_master WHERE type='table')"
    )
    rows = connection.execute(query).fetchall()
    return {str(table): str(token) for table, token in rows}


def ensure_table(connection: sqlite3.Connection, table: Table) -> None:
    """Create a table from its model if needed; the caller commits.

    A watched table missing its triggers or token gets both, so no saved ledger
    trusts it.
    """
    connection.execute(create_table(table))
    if table not in WATCHED_TABLES:
        return
    has_triggers = set(watch_triggers(table)) <= set(get_triggers(connection))
    if not has_triggers or table not in get_revisions(connection):
        watch(connection, table)
        logger.debug("WATCH table '%s' for changes", table)


def watch(connection: sqlite3.Connection, table: Table) -> None:
    """Give a table its change-token triggers and a fresh token; the caller commits."""
    connection.execute(create_revisions())
    for statement in watch_triggers(table).values():
        connection.execute(statement)
    connection.execute(change_token(table))


def add_column_to_table(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
) -> None:
    """Add an extra column to a table."""
    connection.execute(
        f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {EXTRA_COLUMN_TYPE}',
    )
    logger.debug("Added column '%s' to table '%s'", column_name, table_name)


def drop_table(connection: sqlite3.Connection, table_name: str) -> None:
    """Drop a table if it exists."""
    with connection:
        connection.execute(f'DROP TABLE IF EXISTS "{table_name}"')


def _schema_names(connection: sqlite3.Connection, kind: str) -> list[str]:
    """Return the names of every schema object of one kind, such as `table`."""
    cursor = connection.execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))
    return [row[0] for row in cursor.fetchall()]
