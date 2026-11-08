"""The `Txns` table: reads asked for by meaning."""

from __future__ import annotations

from db.queries import earliest_transaction_date, get_connection, get_max_value
from domain import Column, Table


def earliest_date() -> str | None:
    """Return the earliest transaction date, or None for an empty folio."""
    with get_connection() as conn:
        return earliest_transaction_date(conn)


def latest_settlement_date() -> str | None:
    """Return the latest settlement date, or None when the folio holds none."""
    with get_connection() as conn:
        return get_max_value(conn, Table.TXNS, Column.Txn.SETTLE_DATE)
