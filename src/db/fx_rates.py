"""The `FX` table: Bank of Canada rates, as `FxRate` models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from db.connection import get_connection
from db.queries import (
    get_fx_summary,
    get_max_value,
    get_min_value,
    get_records,
    insert_rows,
)
from db.schema import create_fx_table
from domain import Column, Table
from models import FxRate

if TYPE_CHECKING:
    from collections.abc import Sequence


def read() -> list[FxRate]:
    """Return every rate held, ascending by date."""
    with get_connection() as conn:
        rows = get_records(conn, Table.FX, order_by=f'"{Column.FX.DATE}" ASC')
    return [FxRate.from_row(row) for row in rows]


def earliest_date() -> str | None:
    """Return the first date a rate is held for, or None when none is."""
    with get_connection() as conn:
        return get_min_value(conn, Table.FX, Column.FX.DATE)


def latest_date() -> str | None:
    """Return the last date a rate is held for, or None when none is."""
    with get_connection() as conn:
        return get_max_value(conn, Table.FX, Column.FX.DATE)


def write(rates: Sequence[FxRate]) -> int:
    """Append rates, refusing a date already held. No backup: they can be refetched."""
    create_fx_table()
    with get_connection() as conn:
        return len(insert_rows(conn, Table.FX, [rate.to_row() for rate in rates]))


def summary(cutoff: str | None) -> str:
    """Digest the rates a conversion dated on or before `cutoff` can use."""
    with get_connection() as conn:
        return get_fx_summary(conn, cutoff)
