"""The `UpdateBaselines` table: every pool's totals at the latest update."""

from __future__ import annotations

from typing import TYPE_CHECKING

from db.catalog import ensure_table
from db.connection import get_connection
from db.queries import get_records, replace_rows
from domain import Table
from models import UpdateBaseline

if TYPE_CHECKING:
    from collections.abc import Sequence


def read() -> list[UpdateBaseline]:
    """Return every pool's baseline, none before the first is saved."""
    with get_connection() as conn:
        rows = get_records(conn, Table.UPDATE_BASELINES)
    return [UpdateBaseline.from_row(row) for row in rows]


def replace(baselines: Sequence[UpdateBaseline]) -> None:
    """Swap the whole baseline for a new one. No backup: it holds report totals only.

    Args:
        baselines: Every pool's new baseline, at least one.
    """
    with get_connection() as conn:
        ensure_table(conn, Table.UPDATE_BASELINES)
        replace_rows(
            conn,
            Table.UPDATE_BASELINES,
            [baseline.to_row() for baseline in baselines],
            "1 = 1",
            (),
        )
