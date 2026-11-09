"""The `TickerAliases` table: every rename, as `TickerAlias` models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from db.backup import backup_folio
from db.catalog import drop_table, ensure_table
from db.connection import get_connection
from db.queries import (
    delete_rows,
    get_records,
    insert_or_replace_many,
)
from domain import Column, Table
from models import TickerAlias

if TYPE_CHECKING:
    from collections.abc import Sequence

_OLD_TICKER_IS = f'"{Column.Aliases.OLD_TICKER}" = ?'


def read() -> list[TickerAlias]:
    """Return every rename, oldest first so multi-hop chains resolve in order."""
    with get_connection() as conn:
        rows = get_records(
            conn,
            Table.TICKER_ALIASES,
            order_by=f'"{Column.Aliases.EFFECTIVE_DATE}" ASC',
        )
    return [TickerAlias.from_row(row) for row in rows]


def find(old_ticker: str) -> TickerAlias | None:
    """Return the rename of `old_ticker`, or None when it has none."""
    with get_connection() as conn:
        rows = get_records(
            conn,
            Table.TICKER_ALIASES,
            where=_OLD_TICKER_IS,
            params=[old_ticker],
        )
    return TickerAlias.from_row(rows[0]) if rows else None


def write(aliases: Sequence[TickerAlias]) -> None:
    """Back up the folio, then store renames."""
    backup_folio()
    with get_connection() as conn:
        ensure_table(conn, Table.TICKER_ALIASES)
        insert_or_replace_many(
            conn,
            Table.TICKER_ALIASES,
            [alias.to_row() for alias in aliases],
        )


def drop() -> None:
    """Drop the table along with every rename."""
    with get_connection() as conn:
        drop_table(conn, Table.TICKER_ALIASES)


def delete(old_ticker: str) -> int:
    """Back up the folio, then remove a rename, returning how many went."""
    backup_folio()
    with get_connection() as conn:
        return delete_rows(
            conn,
            Table.TICKER_ALIASES,
            where=_OLD_TICKER_IS,
            params=[old_ticker],
        )
