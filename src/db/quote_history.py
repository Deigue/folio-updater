"""The `QuoteHistory` table: condensed charts, as `QuoteHistory` models."""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

from db.connection import get_connection
from db.queries import (
    build_in_clause,
    delete_rows,
    get_records,
    replace_rows,
)
from db.schema import create_quote_history_table
from domain import Column, Table
from models import QuoteHistory
from term import announce

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


def read(symbols: Sequence[str]) -> list[QuoteHistory]:
    """Return the symbols' points in chart order, reading a failure as none."""
    try:
        create_quote_history_table()
        with get_connection() as conn:
            rows = get_records(
                conn,
                Table.QUOTE_HISTORY,
                where=build_in_clause(Column.QuoteHistory.SYMBOL, symbols),
                params=symbols,
                order_by=(
                    f'"{Column.QuoteHistory.SYMBOL}", "{Column.QuoteHistory.RANGE}", '
                    f'"{Column.QuoteHistory.SEQ}"'
                ),
            )
            return [QuoteHistory.from_row(row) for row in rows]
    except (sqlite3.Error, ValueError) as error:
        logger.debug("Could not read the price history: %s", error)
        return []


def replace(symbol: str, points: Sequence[QuoteHistory]) -> None:
    """Swap a symbol's stored points for new ones. No backup: it is a cache.

    Args:
        symbol: The symbol being rewritten.
        points: Its new points, at least one.
    """
    try:
        create_quote_history_table()
        with get_connection() as conn:
            replace_rows(
                conn,
                Table.QUOTE_HISTORY,
                [point.to_row() for point in points],
                where=f'"{Column.QuoteHistory.SYMBOL}" = ?',
                params=[symbol],
            )
    except sqlite3.Error as error:
        announce.warning(f"Could not write the price history: {error}")


def delete(symbols: Sequence[str] | None = None) -> None:
    """Remove stored points.

    Args:
        symbols: The symbols to remove, or None to remove every point.
    """
    try:
        create_quote_history_table()
        with get_connection() as conn:
            delete_rows(
                conn,
                Table.QUOTE_HISTORY,
                where=build_in_clause(Column.QuoteHistory.SYMBOL, symbols),
                params=symbols,
            )
    except sqlite3.Error as error:
        logger.debug("Could not clear the price history: %s", error)
