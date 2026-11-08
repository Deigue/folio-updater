"""Which transactions to read and in what order, with no SQL in it."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TxnOperator(StrEnum):
    """How a filter compares a column with its value."""

    EQUALS = "="
    CONTAINS = "~"
    ABOVE = ">"
    BELOW = "<"
    AT_LEAST = ">="
    AT_MOST = "<="
    ANY_OF = "in"


@dataclass(frozen=True)
class TxnFilter:
    """One condition on one column.

    Attributes:
        column: The column compared.
        operator: How it is compared.
        value: What it is compared with, several values for `ANY_OF`.
    """

    column: str
    operator: TxnOperator
    value: str | tuple[str, ...]


@dataclass(frozen=True)
class TxnSearch:
    """A free-text term, matched against tickers, accounts and text columns.

    Attributes:
        term: The text searched for.
        tickers: The tickers the term may match exactly, renames included.
    """

    term: str
    tickers: tuple[str, ...]


@dataclass(frozen=True)
class TxnSort:
    """One column to sort on.

    Attributes:
        column: The column sorted on.
        descending: Whether the largest comes first.
    """

    column: str
    descending: bool


@dataclass(frozen=True, kw_only=True)
class TxnSelection:
    """Which transactions to read: all conditions must hold.

    Attributes:
        filters: Conditions on single columns.
        searches: Free-text terms.
        sorts: The order, most significant first. Ties fall to the newest date.
        first: Keep only this many from the head of the order.
        last: Keep only this many from the tail of the order. Wins over `first`.
        oldest_first: With no sorts, whether the oldest comes first.
    """

    filters: tuple[TxnFilter, ...] = ()
    searches: tuple[TxnSearch, ...] = ()
    sorts: tuple[TxnSort, ...] = ()
    first: int | None = None
    last: int | None = None
    oldest_first: bool = False
