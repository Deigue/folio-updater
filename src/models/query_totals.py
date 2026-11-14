"""What a `folio query` result adds up to, for the footer under its table."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decimal import Decimal

    from domain import Currency


@dataclass(frozen=True)
class CurrencySums:
    """One currency's sums over a query result, never mixed with another's.

    Attributes:
        currency: The currency summed.
        net_amount: Every amount in it, added with its sign.
        commissions: Commissions charged, rebates taken off, each by its account's
            fee sign.
        average_amount: `net_amount` per transaction, when every result shares one
            action.
        per_month: `net_amount` per calendar month the result spans, when every
            result shares one action.
    """

    currency: Currency
    net_amount: Decimal
    commissions: Decimal
    average_amount: Decimal | None = None
    per_month: Decimal | None = None


@dataclass(frozen=True)
class QueryTotals:
    """The totals over every transaction a query matched, not one page of them.

    Attributes:
        count: How many transactions matched.
        first_date: The earliest transaction date among them.
        last_date: The latest.
        by_currency: Sums per currency, in the order the currencies first appear.
        units: Net units, when every result is the same ticker and none is a split.
        average_price: Price averaged over units, when every result is a buy, or
            every result a sell, of the same ticker.
    """

    count: int
    first_date: str
    last_date: str
    by_currency: tuple[CurrencySums, ...]
    units: Decimal | None = None
    average_price: Decimal | None = None
