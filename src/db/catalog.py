"""Schema reads and changes on a connection the caller opened."""

from __future__ import annotations

from typing import TYPE_CHECKING

from db.ddl import change_token, create_revisions, watch_triggers

if TYPE_CHECKING:
    import sqlite3

    from domain import Table


def get_columns(connection: sqlite3.Connection, table_name: str) -> list[str]:
    """Return list of column names for a table (in defined order)."""
    cursor = connection.execute(f"PRAGMA table_info('{table_name}')")
    return [row[1] for row in cursor.fetchall()]


def get_tables(connection: sqlite3.Connection) -> list[str]:
    """Return list of table names in the database."""
    return _schema_names(connection, "table")


def get_triggers(connection: sqlite3.Connection) -> list[str]:
    """Return list of trigger names in the database."""
    return _schema_names(connection, "trigger")


def watch(connection: sqlite3.Connection, table: Table) -> None:
    """Give a table its change-token triggers and a fresh token; the caller commits."""
    connection.execute(create_revisions())
    for statement in watch_triggers(table).values():
        connection.execute(statement)
    connection.execute(change_token(table))


def _schema_names(connection: sqlite3.Connection, kind: str) -> list[str]:
    """Return the names of every schema object of one kind, such as `table`."""
    cursor = connection.execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))
    return [row[0] for row in cursor.fetchall()]
