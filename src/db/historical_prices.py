"""The historical price tables: daily prices and the days fetched for each symbol."""

from __future__ import annotations

from typing import TYPE_CHECKING

from db.catalog import drop_table, ensure_table
from db.connection import get_connection
from db.queries import (
    build_in_clause,
    get_distinct_set,
    get_historical_price_extents,
    get_records,
    insert_new_rows,
    insert_or_replace_many,
)
from domain import Column, Table
from models import HistoricalPrice, HistoricalPriceCoverage, HistoricalPriceSummary
from models.cells import required_number

if TYPE_CHECKING:
    from collections.abc import Sequence


def read(symbol: str) -> list[HistoricalPrice]:
    """Return a symbol's prices, oldest first."""
    with get_connection() as conn:
        rows = get_records(
            conn,
            Table.HISTORICAL_PRICES,
            where=f'"{Column.HistoricalPrice.SYMBOL}" = ?',
            params=[symbol],
            order_by=f'"{Column.HistoricalPrice.DATE}" ASC',
        )
    return [HistoricalPrice.from_row(row) for row in rows]


def latest_on_or_before(symbol: str, day: str) -> HistoricalPrice | None:
    """Return a symbol's last price dated on or before `day`, or None."""
    with get_connection() as conn:
        rows = get_records(
            conn,
            Table.HISTORICAL_PRICES,
            where=(
                f'"{Column.HistoricalPrice.SYMBOL}" = ? '
                f'AND "{Column.HistoricalPrice.DATE}" <= ?'
            ),
            params=[symbol, day],
            order_by=f'"{Column.HistoricalPrice.DATE}" DESC',
            limit=1,
        )
    return HistoricalPrice.from_row(rows[0]) if rows else None


def coverage(
    symbols: Sequence[str] | None = None,
) -> dict[str, HistoricalPriceCoverage]:
    """Return each symbol's coverage span, or every symbol's when none are named."""
    with get_connection() as conn:
        rows = get_records(
            conn,
            Table.HISTORICAL_PRICE_COVERAGE,
            where=build_in_clause(Column.HistoricalPriceCoverage.SYMBOL, symbols),
            params=symbols,
        )
    spans = [HistoricalPriceCoverage.from_row(row) for row in rows]
    return {span.symbol: span for span in spans}


def priced(symbols: Sequence[str]) -> set[str]:
    """Return which of the symbols have at least one price stored."""
    with get_connection() as conn:
        found = get_distinct_set(
            conn,
            Table.HISTORICAL_PRICES,
            Column.HistoricalPrice.SYMBOL,
        )
    return found & set(symbols)


def summaries(symbols: Sequence[str] | None = None) -> list[HistoricalPriceSummary]:
    """Sum up each covered symbol's prices, sorted; every symbol when none are named."""
    spans = coverage(symbols)
    with get_connection() as conn:
        extents = {row[0]: row for row in get_historical_price_extents(conn)}
    found: list[HistoricalPriceSummary] = []
    for symbol, span in sorted(spans.items()):
        extent = extents.get(symbol)
        if extent is None:
            found.append(HistoricalPriceSummary(span, 0, None, None))
            continue
        _, days, first_day, first_price, last_day, last_price = extent
        found.append(
            HistoricalPriceSummary(
                coverage=span,
                days=days,
                first=_price(symbol, first_day, first_price),
                last=_price(symbol, last_day, last_price),
            ),
        )
    return found


def write(
    prices: Sequence[HistoricalPrice],
    spans: Sequence[HistoricalPriceCoverage],
) -> int:
    """Store new prices, then each symbol's new coverage; returns how many were new.

    A day already held keeps its price. The prices go in first, so a write cut
    short leaves its days uncovered and they are simply fetched again. No
    backup: prices can be fetched again.
    """
    with get_connection() as conn:
        ensure_table(conn, Table.HISTORICAL_PRICES)
        ensure_table(conn, Table.HISTORICAL_PRICE_COVERAGE)
        written = insert_new_rows(
            conn,
            Table.HISTORICAL_PRICES,
            [price.to_row() for price in prices],
        )
        insert_or_replace_many(
            conn,
            Table.HISTORICAL_PRICE_COVERAGE,
            [span.to_row() for span in spans],
        )
    return written


def drop() -> None:
    """Drop both tables, so every symbol is fetched again from scratch."""
    with get_connection() as conn:
        drop_table(conn, Table.HISTORICAL_PRICES)
        drop_table(conn, Table.HISTORICAL_PRICE_COVERAGE)


def _price(symbol: str, day: str, value: object) -> HistoricalPrice:
    """Build a price from a stored day and price cell."""
    return HistoricalPrice(
        symbol=symbol,
        date=day,
        price=required_number(value, Column.HistoricalPrice.PRICE),
    )
