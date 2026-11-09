"""Creating each table from its model on a connection of its own."""

from __future__ import annotations

from db.catalog import ensure_table
from db.connection import get_connection
from domain import Table


def create_txns_table() -> None:
    """Create the transactions table if it does not exist."""
    _create_table(Table.TXNS)


def create_fx_table() -> None:
    """Create the FX rates table if it does not exist."""
    _create_table(Table.FX)


def create_ticker_aliases_table() -> None:
    """Create the ticker aliases table if it does not exist."""
    _create_table(Table.TICKER_ALIASES)


def create_quote_history_table() -> None:
    """Create the price history table if it does not exist."""
    _create_table(Table.QUOTE_HISTORY)


def create_quotes_table() -> None:
    """Create the quotes cache table if it does not exist."""
    _create_table(Table.QUOTES)


def _create_table(table: Table) -> None:
    """Create a table from its model, and watch it if it is a watched table."""
    with get_connection() as conn, conn:
        ensure_table(conn, table)
