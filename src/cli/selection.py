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
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from cli.query_parser import (
    ParsedQuery,
    QueryFilter,
    QueryVocabulary,
    get_valid_column_names,
    parse_query_terms,
)
from db import DatabaseError, aliases, txns
from domain import Column
from models import Txn, TxnFilter, TxnOperator, TxnSearch, TxnSelection, TxnSort
from services.symbols import SymbolResolver

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)


class SelectionError(Exception):
    """A query the folio could not run."""


class SelectionMode(StrEnum):
    """How a set of positional CLI terms was interpreted."""

    IDS = "ids"
    QUERY = "query"


@dataclass(frozen=True)
class Selection:
    """The transactions a command was pointed at, and how they were found.

    Attributes:
        transactions: The matched transactions. Ordered by TxnId in IDS mode, by
            the query's order in QUERY mode.
        mode: Which selection syntax the terms were interpreted as.
        terms: The original CLI terms, kept for audit log lines.
        requested_ids: TxnIds that were asked for. Empty in QUERY mode.
        missing_ids: Requested TxnIds that matched no row.
        query: The parsed query. None in IDS mode.
    """

    transactions: list[Txn]
    mode: SelectionMode
    terms: tuple[str, ...] = ()
    requested_ids: tuple[int, ...] = ()
    missing_ids: tuple[int, ...] = ()
    query: ParsedQuery | None = None

    @property
    def txn_ids(self) -> list[int]:
        """TxnIds of the matched transactions."""
        return [txn.txn_id for txn in self.transactions]

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
        if self.mode is SelectionMode.IDS or self.query is None:
            return f"TxnIds {' '.join(str(i) for i in self.requested_ids)}"
        return f"query: {self.query.describe()}"


def get_ticker_family(ticker: str) -> list[str]:
    """Find all related tickers (aliases) for a given ticker.

    Args:
        ticker: Any symbol in the rename chain.

    Returns:
        Every symbol the security has been known by, including the input.
    """
    return SymbolResolver(aliases.read()).family(ticker)


def load_vocabulary() -> QueryVocabulary:
    """Read the names the folio holds, so a bare query term can be recognised.

    Returns:
        The live Txns columns, and the tickers and accounts in use.
    """
    return QueryVocabulary(
        columns=get_valid_column_names(txns.columns()),
        tickers=txns.tickers(),
        accounts=txns.accounts(),
    )


_OPERATORS = {
    ":": TxnOperator.EQUALS,
    "~": TxnOperator.CONTAINS,
    ">": TxnOperator.ABOVE,
    "<": TxnOperator.BELOW,
    ">=": TxnOperator.AT_LEAST,
    "<=": TxnOperator.AT_MOST,
}


def _txn_filter(item: QueryFilter) -> TxnFilter:
    """Describe one parsed filter; an exact ticker matches its whole rename family."""
    if item.operator == ":" and item.column == Column.Txn.TICKER:
        family = tuple(get_ticker_family(item.value))
        return TxnFilter(item.column, TxnOperator.ANY_OF, family)
    return TxnFilter(item.column, _OPERATORS[item.operator], item.value)


def to_txn_selection(query: ParsedQuery) -> TxnSelection:
    """Describe a parsed query as the transactions it names."""
    return TxnSelection(
        filters=tuple(_txn_filter(item) for item in query.filters),
        searches=tuple(
            TxnSearch(term, tuple(get_ticker_family(term)))
            for term in query.text_searches
        ),
        sorts=tuple(
            TxnSort(sort.column, descending=sort.direction == "desc")
            for sort in query.sorts
        ),
        first=query.start_limit,
        last=query.end_limit,
        oldest_first=query.named_limiter,
    )


def get_transactions_by_filters(query: ParsedQuery) -> list[Txn]:
    """Get transactions from the database based on a ParsedQuery.

    Args:
        query: A ParsedQuery object containing filters, text searches, and sorts.

    Returns:
        The matching transactions.

    Raises:
        SelectionError: If the folio could not run the query.
    """
    try:
        return txns.matching(to_txn_selection(query))
    except DatabaseError as error:
        logger.exception("Error querying transactions")
        msg = "Could not run the query; see the log for details."
        raise SelectionError(msg) from error


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

    Raises:
        SelectionError: If the folio could not run the query.
    """
    txn_ids = _parse_txn_ids(terms)
    if txn_ids is not None:
        transactions = txns.by_ids(txn_ids)
        found = {txn.txn_id for txn in transactions}
        return Selection(
            transactions=transactions,
            mode=SelectionMode.IDS,
            terms=tuple(terms),
            requested_ids=txn_ids,
            missing_ids=tuple(i for i in txn_ids if i not in found),
        )

    query: ParsedQuery = parse_query_terms(terms, load_vocabulary())
    return Selection(
        transactions=get_transactions_by_filters(query),
        mode=SelectionMode.QUERY,
        terms=tuple(terms),
        query=query,
    )
