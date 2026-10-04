"""Read facts back out of whatever folio a test is running against.

Tests that use the demo folio as background must not assume what it holds: the
demo is free to grow or change. They ask the database instead.
"""

from __future__ import annotations

from db import get_alias_edges, get_connection, get_min_value, get_row_count, get_rows
from domain import Action, Column, Table


def txn_total() -> int:
    """Count every stored transaction."""
    with get_connection() as conn:
        return get_row_count(conn, Table.TXNS)


def earliest_txn_date() -> str:
    """Return the first trade date in the folio, in YYYY-MM-DD form."""
    with get_connection() as conn:
        earliest = get_min_value(conn, Table.TXNS, Column.Txn.TXN_DATE)
    assert earliest is not None, "the folio holds no transactions"
    return str(earliest)


def plain_ticker(action: Action = Action.BUY) -> str:
    """Pick the most traded ticker with an `action` row and no rename.

    A renamed ticker makes a query match its whole family, so a test counting
    rows by exact symbol needs one that stands alone.

    Args:
        action: An action the ticker must have at least one row of.

    Returns:
        The ticker symbol.
    """
    with get_connection() as conn:
        renamed = {name for old, new, _ in get_alias_edges(conn) for name in (old, new)}
        rows = get_rows(
            conn,
            Table.TXNS,
            where=f'"{Column.Txn.ACTION}" = ?',
            params=[action.value],
        )
    counts = rows[Column.Txn.TICKER].dropna().value_counts()
    for ticker in counts.index:
        if str(ticker).upper() not in renamed:
            return str(ticker)
    msg = f"no unrenamed ticker has a {action} row"
    raise AssertionError(msg)
