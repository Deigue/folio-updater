"""Numbered changes to existing tables, tracked in `PRAGMA user_version`.

Any change to an existing table's columns or constraints is a new step here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from db.backup import backup_folio
from db.catalog import get_columns, get_tables, watch
from db.ddl import (
    TABLE_MODELS,
    WATCHED_TABLES,
    create_table,
    has_autoincrement,
    model_columns,
)
from domain import Column, Table
from models.columns import stored_columns
from term import announce

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_REBUILDING = "__rebuilding"

# Required Txns columns a migration fills: a missing settlement date is calculated.
_FILLABLE = (Column.Txn.SETTLE_DATE, Column.Txn.SETTLE_CALCULATED)


class MigrationError(Exception):
    """A folio this build cannot bring up to date."""


def bring_up_to_date(conn: sqlite3.Connection) -> None:
    """Back up the folio and run the steps it lacks; a new folio starts at the latest.

    Raises:
        MigrationError: If a newer build migrated it, or a row cannot be carried.
    """
    version = _version(conn)
    if version == LATEST:
        return
    if version > LATEST:
        msg = (
            f"This folio was updated by a newer folio (schema version {version}; "
            f"this one knows {LATEST}). Use the newer folio."
        )
        raise MigrationError(msg)
    if not get_tables(conn):
        _set_version(conn, LATEST)
        return

    announce.info(f"Migrating the folio's schema from version {version} to {LATEST}")
    backup = backup_folio()
    if backup is None:
        announce.warning("No backup was taken first: backups are off or empty folio")
    else:
        announce.info(f"Backed up the folio first: {backup}")
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Another process may have migrated while this one waited for the lock.
        for step in _STEPS[_version(conn) :]:
            step(conn)
        _set_version(conn, LATEST)
    except Exception:
        conn.rollback()
        raise
    conn.commit()
    logger.info("Folio schema is now at version %d", LATEST)


def _typed_txns(conn: sqlite3.Connection) -> None:
    """Step 1: rebuild Txns with typed Fee and Description, keeping IDs and extras.

    Raises:
        MigrationError: If a row lacks a column nothing can fill in.
    """
    tables = get_tables(conn)
    if Table.TXNS in tables:
        for column in _unfillable(Table.TXNS):
            (blank,) = conn.execute(
                f'SELECT COUNT(*) FROM "{Table.TXNS}" WHERE "{column}" IS NULL',
            ).fetchone()
            if blank:
                msg = (
                    f"{blank} transaction(s) have no {column}. Fill it in with a "
                    "database browser, then run folio again."
                )
                raise MigrationError(msg)
        conn.execute(
            f'UPDATE "{Table.TXNS}" SET "{Column.Txn.SETTLE_DATE}" = '
            f'"{Column.Txn.TXN_DATE}", "{Column.Txn.SETTLE_CALCULATED}" = 1 '
            f'WHERE "{Column.Txn.SETTLE_DATE}" IS NULL',
        )
        conn.execute(
            f'UPDATE "{Table.TXNS}" SET "{Column.Txn.SETTLE_CALCULATED}" = 1 '
            f'WHERE "{Column.Txn.SETTLE_CALCULATED}" IS NULL',
        )
        _rebuild(conn, Table.TXNS, keep_extras=True)
    for table in WATCHED_TABLES:
        if table in tables:
            watch(conn, table)


def _quotes_without_value_lists(conn: sqlite3.Connection) -> None:
    """Step 2: rebuild Quotes and QuoteHistory without their allowed-value lists."""
    tables = get_tables(conn)
    for table in (Table.QUOTES, Table.QUOTE_HISTORY):
        if table in tables:
            _rebuild(conn, table, keep_extras=False)


# A folio at version N has run the first N steps.
_STEPS: tuple[Callable[[sqlite3.Connection], None], ...] = (
    _typed_txns,
    _quotes_without_value_lists,
)
LATEST = len(_STEPS)


def _unfillable(table: Table) -> list[str]:
    """Return a table's required columns that no migration can fill in."""
    return [
        column.name
        for column in stored_columns(TABLE_MODELS[table])
        if column.required and column.name not in _FILLABLE
    ]


def _rebuild(conn: sqlite3.Connection, table: Table, *, keep_extras: bool) -> None:
    """Rebuild a table from its model, carrying every row and its counter across."""
    existing = get_columns(conn, table)
    typed = model_columns(table)
    extras = [c for c in existing if c not in typed] if keep_extras else []
    carried = [column for column in (*typed, *extras) if column in existing]
    counter = _counter(conn, table)

    rebuilt = f"{table}{_REBUILDING}"
    conn.execute(create_table(table, name=rebuilt, extras=extras))
    listed = ", ".join(f'"{column}"' for column in carried)
    conn.execute(f'INSERT INTO "{rebuilt}" ({listed}) SELECT {listed} FROM "{table}"')
    conn.execute(f'DROP TABLE "{table}"')
    conn.execute(f'ALTER TABLE "{rebuilt}" RENAME TO "{table}"')
    if counter is not None:
        conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (str(table),))
        conn.execute(
            "INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)",
            (str(table), counter),
        )
    logger.debug("REBUILD table '%s' with %d extra column(s)", table, len(extras))


def _counter(conn: sqlite3.Connection, table: Table) -> int | None:
    """Return where a table's AUTOINCREMENT counter stands, or None without one."""
    if not has_autoincrement(table):
        return None
    row = conn.execute(
        "SELECT seq FROM sqlite_sequence WHERE name = ?",
        (str(table),),
    ).fetchone()
    return None if row is None else int(row[0])


def _version(conn: sqlite3.Connection) -> int:
    """Read the last step the folio ran."""
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    return int(version)


def _set_version(conn: sqlite3.Connection, version: int) -> None:
    """Record the last step the folio ran."""
    conn.execute(f"PRAGMA user_version = {int(version)}")
