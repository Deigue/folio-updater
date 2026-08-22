"""Transaction selection engine for the folio CLI.

Turns a `ParsedQuery` into rows, and resolves the positional terms that
`query`, `delete` and `edit` all accept into the transactions they point at.
Selection accepts either explicit TxnIds or query terms, so the mutating
commands reuse the query engine rather than inventing a second syntax.

`cli.query_parser` handles text -> `ParsedQuery`; this module handles
`ParsedQuery` -> rows. Deliberately free of `typer`, so it stays usable and
testable outside the CLI.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

import pandas as pd

from app.app_context import get_config
from cli.query_parser import ParsedQuery, parse_query_terms
from db.queries import (
    build_comparison_clause,
    build_sort_clause,
    get_alias_edges,
    get_columns,
    get_connection,
    get_rows,
)
from services.symbols import SymbolResolver
from utils.constants import Column, Table
from utils.optional_fields import FieldType

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


class SelectionMode(StrEnum):
    """How a set of positional CLI terms was interpreted."""

    IDS = "ids"
    QUERY = "query"


@dataclass(frozen=True)
class Selection:
    """The transactions a command was pointed at, and how they were found.

    Attributes:
        transactions: Matched rows with the full set of Txns columns. Ordered by
            TxnId in IDS mode, by the query's ORDER BY in QUERY mode.
        mode: Which selection syntax the terms were interpreted as.
        terms: The original CLI terms, kept for audit log lines.
        requested_ids: TxnIds that were asked for. Empty in QUERY mode.
        missing_ids: Requested TxnIds that matched no row.
        query: The parsed query. None in IDS mode.
    """

    transactions: pd.DataFrame
    mode: SelectionMode
    terms: tuple[str, ...] = ()
    requested_ids: tuple[int, ...] = ()
    missing_ids: tuple[int, ...] = ()
    query: ParsedQuery | None = None

    @property
    def txn_ids(self) -> list[int]:
        """TxnIds of the matched transactions."""
        if self.transactions.empty:
            return []
        return [int(txn_id) for txn_id in self.transactions[Column.Txn.TXN_ID]]

    @property
    def is_unbounded(self) -> bool:
        """Whether the selection would sweep the entire transactions table."""
        if self.mode is SelectionMode.IDS or self.query is None:
            return False
        return not (
            self.query.filters
            or self.query.text_searches
            or self.query.start_limit
            or self.query.end_limit
        )

    def describe(self) -> str:
        """Describe the selection for an audit log header."""
        if self.mode is SelectionMode.IDS:
            return f"TxnIds {' '.join(str(i) for i in self.requested_ids)}"
        return f"query: {self.query!r}"


def get_ticker_family(connection: sqlite3.Connection, ticker: str) -> list[str]:
    """Find all related tickers (aliases) for a given ticker.

    Args:
        connection: The database connection.
        ticker: Any symbol in the rename chain.

    Returns:
        Every symbol the security has been known by, including the input.
    """
    return SymbolResolver(get_alias_edges(connection)).family(ticker)


def _get_optional_columns(
    conn: sqlite3.Connection,
    field_type: FieldType,
) -> list[str]:
    """Get configured optional columns of one type that are live on Txns."""
    live_columns = set(get_columns(conn, Table.TXNS))
    optional_fields = get_config().optional_fields
    return [
        name
        for name, field_def in optional_fields.get_all_fields().items()
        if field_def.field_type == field_type and name in live_columns
    ]


def _get_text_numeric_columns(conn: sqlite3.Connection) -> set[str]:
    """Get the numeric columns that SQLite holds as TEXT.

    Set of table columns that are configured as numeric and will require an
    explicit CAST.

    Args:
        conn: The database connection.

    Returns:
        Names of the live Txns columns that hold numbers as text.
    """
    columns = set(_get_optional_columns(conn, FieldType.NUMERIC))
    if str(Column.Txn.FEE) in set(get_columns(conn, Table.TXNS)):
        columns.add(str(Column.Txn.FEE))
    return columns


def build_where_clause(
    query: ParsedQuery,
    conn: sqlite3.Connection,
) -> tuple[str, list[str | int | float]]:
    """Build the WHERE clause and parameters for the transaction query."""
    where_clauses: list[str] = []
    params: list[str | int | float] = []
    optional_text_columns = _get_optional_columns(conn, FieldType.STRING)
    numeric_columns = _get_text_numeric_columns(conn)

    # Process each filter
    for f in query.filters:
        if f.operator == ":":
            if f.column == Column.Txn.TICKER:
                ticker_family = get_ticker_family(conn, f.value)
                if ticker_family:
                    placeholders = ",".join("?" * len(ticker_family))
                    where_clauses.append(f'"{f.column}" IN ({placeholders})')
                    params.extend(ticker_family)
            else:
                clause, param = build_comparison_clause(
                    f.column,
                    "=",
                    f.value,
                    text_numeric=f.column in numeric_columns,
                )
                where_clauses.append(clause)
                params.append(param)
        elif f.operator == "~":
            where_clauses.append(f'"{f.column}" LIKE ?')
            params.append(f"%{f.value}%")
        elif f.operator in (">", "<", ">=", "<="):
            clause, param = build_comparison_clause(
                f.column,
                f.operator,
                f.value,
                text_numeric=f.column in numeric_columns,
            )
            where_clauses.append(clause)
            params.append(param)

    # Process text searches
    for search in query.text_searches:
        optional_clauses = [f'"{col}" LIKE ?' for col in optional_text_columns]
        optional_params = [f"%{search}%" for _ in optional_text_columns]

        # get_ticker_family always includes the search term itself, so this
        # is never empty.
        ticker_family = get_ticker_family(conn, search)
        placeholders = ",".join("?" * len(ticker_family))
        ticker_clause = f'"{Column.Txn.TICKER}" IN ({placeholders})'
        account_clause = f'"{Column.Txn.ACCOUNT}" LIKE ?'
        clauses = [ticker_clause, account_clause, *optional_clauses]
        where_clauses.append(f"({' OR '.join(clauses)})")
        params.extend(ticker_family)
        params.append(f"%{search}%")
        params.extend(optional_params)

    where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
    return where_clause, params


def build_order_by_clause(query: ParsedQuery, conn: sqlite3.Connection) -> str:
    """Build the ORDER BY clause for the transaction query.

    Args:
        query: The parsed query whose sorts drive the ordering.
        conn: The database connection, used to find TEXT-stored numeric columns.

    Returns:
        The ORDER BY clause, always ending in a TxnDate tiebreak.
    """
    if not query.sorts:
        if query.named_limiter:
            return f'"{Column.Txn.TXN_DATE}" ASC'
        return f'"{Column.Txn.TXN_DATE}" DESC'

    numeric_columns = _get_text_numeric_columns(conn)
    order_parts = []
    for s in query.sorts:
        direction = "DESC" if s.direction == "desc" else "ASC"
        order_parts.append(
            build_sort_clause(
                s.column,
                direction,
                text_numeric=s.column in numeric_columns,
            ),
        )

    if not any(s.column == Column.Txn.TXN_DATE for s in query.sorts):
        order_parts.append(f'"{Column.Txn.TXN_DATE}" DESC')

    return ", ".join(order_parts)


def _invert_order_by(order_by_clause: str) -> str:
    """Flip ASC/DESC on every term of an ORDER BY clause.

    Used to fetch the tail of a sort order via `LIMIT`: querying with the
    inverted order and reverse result is equivalent to (but far cheaper
    than) fetching everything and slicing off the last N rows.
    """
    inverted_parts = []
    for raw_part in order_by_clause.split(","):
        part = raw_part.strip()
        if part.endswith(" DESC"):
            inverted_parts.append(part.removesuffix(" DESC") + " ASC")
        else:
            inverted_parts.append(part.removesuffix(" ASC") + " DESC")
    return ", ".join(inverted_parts)


def get_transactions_by_filters(query: ParsedQuery) -> pd.DataFrame:
    """Get transactions from the database based on a ParsedQuery.

    Args:
        query: A ParsedQuery object containing filters, text searches, and sorts.

    Returns:
        A DataFrame with matching transactions.
    """
    try:
        with get_connection() as conn:
            where_clause, params = build_where_clause(query, conn)
            order_by_clause = build_order_by_clause(query, conn)

            if query.end_limit is not None:
                # fetch with the order inverted, then reverse back to restore sort.
                rows = get_rows(
                    conn,
                    Table.TXNS,
                    where=where_clause,
                    params=params,
                    order_by=_invert_order_by(order_by_clause),
                    limit=query.end_limit,
                )
                return rows.iloc[::-1].reset_index(drop=True)

            return get_rows(
                conn,
                Table.TXNS,
                where=where_clause,
                params=params,
                order_by=order_by_clause,
                limit=query.start_limit,
            )
    except (sqlite3.OperationalError, pd.errors.DatabaseError):
        logger.exception("Error querying transactions")
        return pd.DataFrame()


def _parse_txn_ids(terms: Sequence[str]) -> tuple[int, ...] | None:
    """Interpret terms as an explicit TxnId list, if they all look like ids.

    Args:
        terms: Positional CLI terms.

    Returns:
        Deduplicated TxnIds in the order given, or None if any term is not a
        plain positive integer.
    """
    if not terms or not all(term.isdigit() for term in terms):
        return None

    seen: dict[int, None] = {}
    for term in terms:
        seen.setdefault(int(term), None)
    return tuple(seen)


def _get_transactions_by_ids(txn_ids: Sequence[int]) -> pd.DataFrame:
    """Fetch transactions for an explicit list of TxnIds, ordered by TxnId."""
    placeholders = ",".join("?" * len(txn_ids))
    with get_connection() as conn:
        return get_rows(
            conn,
            Table.TXNS,
            where=f'"{Column.Txn.TXN_ID}" IN ({placeholders})',
            params=list(txn_ids),
            order_by=f'"{Column.Txn.TXN_ID}" ASC',
        )


def select_transactions(terms: Sequence[str]) -> Selection:
    """Resolve positional CLI terms into the transactions they point at.

    When every term is a plain integer the terms are explicit TxnIds;
    otherwise they are parsed as query terms and run through the query engine.
    A bare year must therefore be written explicitly (`date:2024`) to avoid
    being read as a TxnId.

    Args:
        terms: Positional terms from the CLI.

    Returns:
        A Selection describing the matched rows and how they were found.
    """
    txn_ids = _parse_txn_ids(terms)
    if txn_ids is not None:
        transactions = _get_transactions_by_ids(txn_ids)
        found = set(transactions[Column.Txn.TXN_ID].astype(int))
        return Selection(
            transactions=transactions,
            mode=SelectionMode.IDS,
            terms=tuple(terms),
            requested_ids=txn_ids,
            missing_ids=tuple(i for i in txn_ids if i not in found),
        )

    query: ParsedQuery = parse_query_terms(terms)
    return Selection(
        transactions=get_transactions_by_filters(query),
        mode=SelectionMode.QUERY,
        terms=tuple(terms),
        query=query,
    )
