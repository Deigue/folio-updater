"""Query command for the folio CLI."""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

import pandas as pd

from app import bootstrap
from app.app_context import get_config
from cli.display import page_transactions
from cli.query_parser import ParsedQuery, parse_query_terms
from db.queries import get_columns, get_connection, get_rows, get_tables
from utils.constants import Column, Table
from utils.log_console import info_both
from utils.optional_fields import FieldType

if TYPE_CHECKING:
    from cli.query_parser import ParsedQuery

logger = logging.getLogger(__name__)


def _get_ticker_family(connection: sqlite3.Connection, ticker: str) -> list[str]:
    """Find all related tickers (aliases) for a given ticker."""
    all_aliases = {ticker.upper()}
    new_found = True

    # Check if alias table exists
    tables = get_tables(connection)
    if Table.TICKER_ALIASES not in tables:
        return list(all_aliases)

    while new_found:
        new_found = False
        current_aliases = list(all_aliases)
        placeholders = ",".join("?" for _ in current_aliases)

        # Find new tickers from old
        query_new = (
            f"SELECT NewTicker FROM {Table.TICKER_ALIASES} "
            f"WHERE OldTicker IN ({placeholders})"
        )
        new_tickers = pd.read_sql_query(
            query_new,
            connection,
            params=tuple(current_aliases),
        )
        for new in new_tickers["NewTicker"]:
            if new not in all_aliases:
                all_aliases.add(new)
                new_found = True

        # Find old tickers from new
        query_old = (
            f"SELECT OldTicker FROM {Table.TICKER_ALIASES} "
            f"WHERE NewTicker IN ({placeholders})"
        )
        old_tickers = pd.read_sql_query(
            query_old,
            connection,
            params=tuple(current_aliases),
        )
        for old in old_tickers["OldTicker"]:
            if old not in all_aliases:
                all_aliases.add(old)
                new_found = True

    return list(all_aliases)


def _get_optional_text_columns(conn: sqlite3.Connection) -> list[str]:
    """Get configured optional string columns that are live on the Txns table."""
    live_columns = set(get_columns(conn, Table.TXNS))
    optional_fields = get_config().optional_fields
    return [
        name
        for name, field in optional_fields.get_all_fields().items()
        if field.field_type == FieldType.STRING and name in live_columns
    ]


def _build_query_where_clause(
    query: ParsedQuery,
    conn: sqlite3.Connection,
) -> tuple[str, list[str | int | float]]:
    """Build the WHERE clause and parameters for the transaction query."""
    where_clauses: list[str] = []
    params: list[str | int | float] = []
    optional_text_columns = _get_optional_text_columns(conn)

    # Process each filter
    for f in query.filters:
        if f.operator == ":":
            if f.column == Column.Txn.TICKER:
                ticker_family = _get_ticker_family(conn, f.value)
                if ticker_family:
                    placeholders = ",".join("?" * len(ticker_family))
                    where_clauses.append(f'"{f.column}" IN ({placeholders})')
                    params.extend(ticker_family)
            else:
                where_clauses.append(f'"{f.column}" = ?')
                params.append(f.value)
        elif f.operator == "~":
            where_clauses.append(f'"{f.column}" LIKE ?')
            params.append(f"%{f.value}%")
        elif f.operator in (">", "<", ">=", "<="):
            where_clauses.append(f'"{f.column}" {f.operator} ?')
            params.append(f.value)

    # Process text searches
    for search in query.text_searches:
        optional_clauses = [f'"{col}" LIKE ?' for col in optional_text_columns]
        optional_params = [f"%{search}%" for _ in optional_text_columns]

        ticker_family = _get_ticker_family(conn, search)
        if ticker_family:
            placeholders = ",".join("?" * len(ticker_family))
            ticker_clause = f'"{Column.Txn.TICKER}" IN ({placeholders})'
            account_clause = f'"{Column.Txn.ACCOUNT}" LIKE ?'
            clauses = [ticker_clause, account_clause, *optional_clauses]
            where_clauses.append(f"({' OR '.join(clauses)})")
            params.extend(ticker_family)
            params.append(f"%{search}%")
            params.extend(optional_params)
        else:
            clauses = [
                f'"{Column.Txn.TICKER}" LIKE ?',
                f'"{Column.Txn.ACCOUNT}" LIKE ?',
                *optional_clauses,
            ]
            where_clauses.append(f"({' OR '.join(clauses)})")
            params.extend([f"%{search}%", f"%{search}%"])
            params.extend(optional_params)

    where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"
    return where_clause, params


def _build_query_order_by_clause(query: ParsedQuery) -> str:
    """Build the ORDER BY clause for the transaction query."""
    if not query.sorts:
        return f'"{Column.Txn.TXN_DATE}" DESC'

    order_parts = []
    for s in query.sorts:
        direction = "DESC" if s.direction == "desc" else "ASC"
        order_parts.append(f'"{s.column}" {direction}')

    if not any(s.column == Column.Txn.TXN_DATE for s in query.sorts):
        order_parts.append(f'"{Column.Txn.TXN_DATE}" DESC')

    return ", ".join(order_parts)


def _get_transactions_by_filters(query: ParsedQuery) -> pd.DataFrame:
    """Get transactions from the database based on a ParsedQuery.

    Args:
        query: A ParsedQuery object containing filters, text searches, and sorts.

    Returns:
        A DataFrame with matching transactions.
    """
    try:
        with get_connection() as conn:
            where_clause, params = _build_query_where_clause(query, conn)
            order_by_clause = _build_query_order_by_clause(query)
            return get_rows(
                conn,
                Table.TXNS,
                where=where_clause,
                params=params,
                order_by=order_by_clause,
                limit=query.limit,
            )
    except (sqlite3.OperationalError, pd.errors.DatabaseError):
        logger.exception("Error querying transactions")
        return pd.DataFrame()


def query_transactions(terms: list[str]) -> None:
    """Query transactions from the database.

    Args:
        terms: A list of query terms from the CLI.
    """
    bootstrap.reload_config()
    info_both(f"Parsing query terms: {terms}")
    query: ParsedQuery = parse_query_terms(terms)
    info_both(f"{query}")

    results_df = _get_transactions_by_filters(query)

    if results_df.empty:
        info_both("No transactions found matching the criteria.")
    else:
        info_both(f"Found {len(results_df)} matching transaction(s).")
        page_transactions(results_df, title="Query Results")
