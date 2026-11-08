"""The `Txns` table: reads asked for by meaning, handing back `Txn` models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app import get_config
from config.optional_fields import FieldType
from db.backup import backup_folio
from db.catalog import get_columns
from db.queries import (
    add_column_to_table,
    build_comparison_clause,
    build_in_clause,
    build_sort_clause,
    delete_rows,
    drop_table,
    earliest_transaction_date,
    get_broker_latest_txn_date,
    get_connection,
    get_distinct_set,
    get_distinct_values,
    get_max_value,
    get_records,
    get_row_count,
    insert_rows,
    trial_insert_rows,
    update_rows,
)
from domain import Column, Table
from models import Txn, TxnOperator, TxnSort

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Sequence

    from models import NewTxn, TxnFilter, TxnSearch, TxnSelection

_TXN_ID = f'"{Column.Txn.TXN_ID}"'
_IS_CALCULATED = f'"{Column.Txn.SETTLE_CALCULATED}" = ?'


def _txns(rows: list[dict[str, object]]) -> list[Txn]:
    """Turn stored rows into transactions."""
    return [Txn.from_row(row) for row in rows]


def read() -> list[Txn]:
    """Return every transaction, by transaction date then TxnId."""
    with get_connection() as conn:
        return _txns(
            get_records(
                conn,
                Table.TXNS,
                order_by=f'"{Column.Txn.TXN_DATE}", {_TXN_ID}',
            ),
        )


def by_ids(txn_ids: Sequence[int]) -> list[Txn]:
    """Return the transactions with these TxnIds, in TxnId order."""
    with get_connection() as conn:
        return _txns(
            get_records(
                conn,
                Table.TXNS,
                where=build_in_clause(Column.Txn.TXN_ID, txn_ids),
                params=txn_ids,
                order_by=_TXN_ID,
            ),
        )


def since(txn_id: int) -> list[Txn]:
    """Return the transactions stored after a TxnId, oldest first."""
    with get_connection() as conn:
        return _txns(
            get_records(
                conn,
                Table.TXNS,
                where=f"{_TXN_ID} > ?",
                params=[txn_id],
                order_by=_TXN_ID,
            ),
        )


def matching(selection: TxnSelection) -> list[Txn]:
    """Return the transactions a selection names, in its order."""
    with get_connection() as conn:
        live = get_columns(conn, Table.TXNS)
        numeric = _numbers_held_as_text(live)
        where, params = _where(selection, live, numeric)
        sorts = _sorts(selection)
        if selection.last is not None:
            # The tail of an order is the head of its reverse, flipped back.
            rows = get_records(
                conn,
                Table.TXNS,
                where=where,
                params=params,
                order_by=_order_by(sorts, numeric, flip=True),
                limit=selection.last,
            )
            rows.reverse()
        else:
            rows = get_records(
                conn,
                Table.TXNS,
                where=where,
                params=params,
                order_by=_order_by(sorts, numeric),
                limit=selection.first,
            )
    return _txns(rows)


def calculated_settlements(broker: str | None = None) -> list[Txn]:
    """Return those whose settlement date is calculated, soonest settling first.

    Args:
        broker: Narrow to accounts whose name holds this, in any case.

    Returns:
        The transactions, by settlement date then TxnId.
    """
    where = _IS_CALCULATED
    params: list[object] = [1]
    if broker is not None:
        where += f' AND UPPER("{Column.Txn.ACCOUNT}") LIKE ?'
        params.append(f"%{broker.upper()}%")
    with get_connection() as conn:
        return _txns(
            get_records(
                conn,
                Table.TXNS,
                where=where,
                params=params,
                order_by=f'"{Column.Txn.SETTLE_DATE}", {_TXN_ID}',
            ),
        )


def count() -> int:
    """Count every transaction."""
    with get_connection() as conn:
        return get_row_count(conn, Table.TXNS)


def count_calculated() -> int:
    """Count the transactions whose settlement date is calculated."""
    with get_connection() as conn:
        return get_row_count(conn, Table.TXNS, where=_IS_CALCULATED, params=[1])


def latest_id() -> int:
    """Return the highest TxnId, or 0 for an empty folio."""
    with get_connection() as conn:
        latest = get_max_value(conn, Table.TXNS, Column.Txn.TXN_ID)
    return int(latest) if latest else 0


def traded_tickers() -> list[str]:
    """Return every ticker the transactions carry, sorted."""
    ticker = f'"{Column.Txn.TICKER}"'
    with get_connection() as conn:
        values = get_distinct_values(
            conn,
            Table.TXNS,
            Column.Txn.TICKER,
            filter_condition=f"{ticker} IS NOT NULL AND {ticker} != ''",
            order_by=ticker,
        )
    return [str(value) for value in values]


def tickers() -> set[str]:
    """Return every ticker in use."""
    with get_connection() as conn:
        return get_distinct_set(conn, Table.TXNS, Column.Txn.TICKER)


def accounts() -> set[str]:
    """Return every account in use."""
    with get_connection() as conn:
        return get_distinct_set(conn, Table.TXNS, Column.Txn.ACCOUNT)


def columns() -> list[str]:
    """Return the table's columns, any an import added included."""
    with get_connection() as conn:
        return get_columns(conn, Table.TXNS)


def earliest_date() -> str | None:
    """Return the earliest transaction date, or None for an empty folio."""
    with get_connection() as conn:
        return earliest_transaction_date(conn)


def latest_settlement_date() -> str | None:
    """Return the latest settlement date, or None when the folio holds none."""
    with get_connection() as conn:
        return get_max_value(conn, Table.TXNS, Column.Txn.SETTLE_DATE)


def latest_date_for(broker: str) -> str | None:
    """Return a broker's latest transaction date, or None when it has none."""
    with get_connection() as conn:
        return get_broker_latest_txn_date(conn, broker)


def append(new: Sequence[NewTxn]) -> list[int]:
    """Back up, then store transactions (all or none), returning their TxnIds."""
    if not new:
        return []
    backup_folio()
    with get_connection() as conn:
        _add_extra_columns(conn, new)
        return insert_rows(conn, Table.TXNS, [txn.to_row() for txn in new])


def update(changed: Sequence[Txn]) -> int:
    """Back up, then rewrite transactions whole by TxnId, returning how many."""
    if not changed:
        return 0
    backup_folio()
    rows = [txn.to_row() for txn in changed]
    columns = [column for column in rows[0] if column != Column.Txn.TXN_ID]
    with get_connection() as conn:
        return update_rows(conn, Table.TXNS, rows, [Column.Txn.TXN_ID], columns)


def delete(txn_ids: Sequence[int]) -> int:
    """Back up the folio, then remove transactions, returning how many went."""
    backup_folio()
    with get_connection() as conn:
        return delete_rows(
            conn,
            Table.TXNS,
            where=build_in_clause(Column.Txn.TXN_ID, txn_ids),
            params=txn_ids,
        )


def trial_append(new: Sequence[NewTxn]) -> list[sqlite3.IntegrityError | None]:
    """Try transactions one at a time, storing none, to find which the table refuses.

    Returns:
        One entry per transaction tried: None for one that went in, the error for
        the one refused, which is the last entry.
    """
    with get_connection() as conn:
        return trial_insert_rows(conn, Table.TXNS, [txn.to_row() for txn in new])


def drop() -> None:
    """Drop the table, so the next transaction starts again from TxnId 1."""
    with get_connection() as conn:
        drop_table(conn, Table.TXNS)


def _add_extra_columns(conn: sqlite3.Connection, new: Sequence[NewTxn]) -> None:
    """Give the table a column for every extra these transactions carry."""
    live = get_columns(conn, Table.TXNS)
    for column in dict.fromkeys(name for txn in new for name in txn.extras):
        if column not in live:
            add_column_to_table(conn, Table.TXNS, column)


def _numbers_held_as_text(live: list[str]) -> set[str]:
    """Name the live columns whose numbers SQLite must be told to compare as numbers."""
    found = set(get_config().optional_fields.columns_of_type(FieldType.NUMERIC, live))
    if str(Column.Txn.FEE) in live:
        found.add(str(Column.Txn.FEE))
    return found


def _where(
    selection: TxnSelection,
    live: list[str],
    numeric: set[str],
) -> tuple[str, list[object]]:
    """Build a selection's WHERE clause and its parameters."""
    text_columns = get_config().optional_fields.columns_of_type(FieldType.STRING, live)
    clauses: list[str] = []
    params: list[object] = []
    for item in selection.filters:
        clause, bound = _filter_clause(item, numeric)
        clauses.append(clause)
        params.extend(bound)
    for search in selection.searches:
        clause, bound = _search_clause(search, text_columns)
        clauses.append(clause)
        params.extend(bound)
    return " AND ".join(clauses) or "1=1", params


def _filter_clause(item: TxnFilter, numeric: set[str]) -> tuple[str, list[object]]:
    """Build one filter's clause and its parameters."""
    if isinstance(item.value, tuple):
        clause = build_in_clause(item.column, item.value)
        return str(clause), list(item.value)
    if item.operator is TxnOperator.CONTAINS:
        return f'"{item.column}" LIKE ?', [f"%{item.value}%"]
    clause, param = build_comparison_clause(
        item.column,
        item.operator.value,
        item.value,
        text_numeric=item.column in numeric,
    )
    return clause, [param]


def _search_clause(
    search: TxnSearch,
    text_columns: Sequence[str],
) -> tuple[str, list[object]]:
    """Build one free-text term's clause and its parameters."""
    like = f"%{search.term}%"
    clauses = [
        str(build_in_clause(Column.Txn.TICKER, search.tickers)),
        f'"{Column.Txn.ACCOUNT}" LIKE ?',
        *(f'"{column}" LIKE ?' for column in text_columns),
    ]
    params = [*search.tickers, like, *(like for _ in text_columns)]
    return f"({' OR '.join(clauses)})", params


def _sorts(selection: TxnSelection) -> list[TxnSort]:
    """List a selection's order, ending in a date tiebreak."""
    date = str(Column.Txn.TXN_DATE)
    if not selection.sorts:
        return [TxnSort(date, descending=not selection.oldest_first)]
    sorts = list(selection.sorts)
    if not any(sort.column == date for sort in sorts):
        sorts.append(TxnSort(date, descending=True))
    return sorts


def _order_by(
    sorts: list[TxnSort],
    numeric: set[str],
    *,
    flip: bool = False,
) -> str:
    """Render sorts as an ORDER BY clause, optionally reversed."""
    return ", ".join(
        build_sort_clause(
            sort.column,
            "DESC" if sort.descending != flip else "ASC",
            text_numeric=sort.column in numeric,
        )
        for sort in sorts
    )
