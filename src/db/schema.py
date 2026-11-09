"""Creating each table from its model the first time it is needed."""

from __future__ import annotations

import logging
import sqlite3

from db.catalog import get_triggers, watch
from db.connection import get_connection
from db.ddl import create_table, watch_triggers
from db.queries import get_revisions
from domain import Table

logger = logging.getLogger(__name__)


def create_txns_table() -> None:
    """Create the transactions table if it does not exist."""
    _create_table(Table.TXNS)
    _watch(Table.TXNS)


def create_fx_table() -> None:
    """Create the FX rates table if it does not exist."""
    _create_table(Table.FX)


def create_ticker_aliases_table() -> None:
    """Create the ticker aliases table if it does not exist."""
    _create_table(Table.TICKER_ALIASES)
    _watch(Table.TICKER_ALIASES)


def create_quote_history_table() -> None:
    """Create the price history table if it does not exist."""
    _create_table(Table.QUOTE_HISTORY)


def create_quotes_table() -> None:
    """Create the quotes cache table if it does not exist."""
    _create_table(Table.QUOTES)


def _watch(table: Table) -> None:
    """Give a table missing its triggers or token both, so no saved ledger trusts it."""
    triggers = watch_triggers(table)
    with get_connection() as conn:
        watched = set(triggers) <= set(get_triggers(conn))
        if watched and table in get_revisions(conn):
            return

    with get_connection() as conn:
        watch(conn, table)
        conn.commit()
    logger.debug("WATCH table '%s' for changes", table)


def _create_table(table: Table) -> None:
    """Create a table from its model if it does not exist yet."""
    try:
        with get_connection() as conn:
            conn.execute(create_table(table))
            logger.debug("CREATE table '%s'", table)
    except sqlite3.DatabaseError as e:
        msg = f"Failed to create table '{table}': {e}"
        logger.exception(msg)
        raise
