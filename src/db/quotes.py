"""The `Quotes` table: the cache of provider prices, as `Quote` models."""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

from db.catalog import ensure_table
from db.connection import get_connection
from db.queries import (
    build_in_clause,
    delete_rows,
    get_records,
    insert_or_replace_many,
)
from domain import Column, Table
from models import Quote
from term import announce

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


def read(symbols: Sequence[str] | None = None) -> dict[str, Quote]:
    """Return cached quotes by symbol, reading a cache that cannot be read as empty.

    Args:
        symbols: The symbols to read, or None for every cached row.

    Returns:
        Each symbol found, mapped to its quote.
    """
    try:
        with get_connection() as conn:
            rows = get_records(
                conn,
                Table.QUOTES,
                where=build_in_clause(Column.Quote.SYMBOL, symbols),
                params=symbols,
            )
    except sqlite3.Error as error:
        logger.debug("Could not read the quotes cache: %s", error)
        return {}
    quotes = [Quote.from_row(row) for row in rows]
    return {quote.symbol: quote for quote in quotes}


def write(quotes: Sequence[Quote]) -> None:
    """Store quotes over any of the same symbol. No backup: it is a cache."""
    try:
        with get_connection() as conn:
            ensure_table(conn, Table.QUOTES)
            insert_or_replace_many(
                conn,
                Table.QUOTES,
                [quote.to_row() for quote in quotes],
            )
    except sqlite3.Error as error:
        announce.warning(f"Could not write the quotes cache: {error}")


def delete(symbols: Sequence[str] | None = None) -> int:
    """Remove cached quotes.

    Args:
        symbols: The symbols to remove, or None to empty the cache.

    Returns:
        Number of quotes removed.
    """
    try:
        with get_connection() as conn:
            return delete_rows(
                conn,
                Table.QUOTES,
                where=build_in_clause(Column.Quote.SYMBOL, symbols),
                params=symbols,
            )
    except sqlite3.Error as error:
        logger.debug("Could not clear the quotes cache: %s", error)
        return 0
