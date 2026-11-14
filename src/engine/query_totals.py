"""Adding up a query's transactions: counts, sums per currency and averages."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from domain import Action
from domain.numeric import ZERO
from engine.accounts import resolve_account
from models.query_totals import CurrencySums, QueryTotals

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from decimal import Decimal

    from domain import Currency
    from engine.fees import FeeRules
    from models import Txn

_PRICED_TRADES = (Action.BUY, Action.SELL)


def resolved_fees(
    accounts: Iterable[str],
    stored: Sequence[Txn],
) -> dict[str, FeeRules]:
    """Settle each account's fee rules from every transaction it has stored.

    A setting left on auto is read from the account's own trades, so the rows a
    query happened to match are not enough to settle it.
    """
    return {
        name: resolve_account(name).fees.resolve(
            name,
            [txn for txn in stored if txn.account == name],
        )
        for name in sorted(set(accounts))
    }


def query_totals(
    txns: Sequence[Txn],
    fees: Mapping[str, FeeRules],
) -> QueryTotals | None:
    """Add up a query's transactions, or None when it matched nothing.

    Args:
        txns: Every transaction the query matched.
        fees: Settled fee rules for each account whose transactions carry a fee.

    Returns:
        The totals.
    """
    if not txns:
        return None
    first = min(txn.txn_date for txn in txns)
    last = max(txn.txn_date for txn in txns)
    one_action = len({txn.action for txn in txns}) == 1
    months = _months_spanned(first, last)

    sums: dict[Currency, list[Txn]] = {}
    for txn in txns:
        sums.setdefault(txn.currency, []).append(txn)

    by_currency = tuple(
        _currency_sums(currency, rows, fees, months if one_action else None)
        for currency, rows in sums.items()
    )
    tickers = {txn.ticker for txn in txns}
    one_ticker = len(tickers) == 1 and None not in tickers
    return QueryTotals(
        count=len(txns),
        first_date=first,
        last_date=last,
        by_currency=by_currency,
        units=_net_units(txns) if one_ticker else None,
        average_price=_average_price(txns) if one_ticker and one_action else None,
    )


def _currency_sums(
    currency: Currency,
    rows: Sequence[Txn],
    fees: Mapping[str, FeeRules],
    months: int | None,
) -> CurrencySums:
    """Sum one currency's rows; `months` is set only when every row shares an action."""
    net = sum((txn.amount or ZERO for txn in rows), ZERO)
    commissions = sum(
        (fees[txn.account].commission(txn) for txn in rows if txn.fee),
        ZERO,
    )
    return CurrencySums(
        currency=currency,
        net_amount=net,
        commissions=commissions,
        average_amount=None if months is None else net / len(rows),
        per_month=None if months is None else net / months,
    )


def _months_spanned(first: str, last: str) -> int:
    """Count the calendar months from `first` to `last`, both included."""
    start, end = date.fromisoformat(first), date.fromisoformat(last)
    return (end.year - start.year) * 12 + end.month - start.month + 1


def _net_units(txns: Sequence[Txn]) -> Decimal | None:
    """Add up one ticker's units, or None when a split's units would be misread."""
    if any(txn.action is Action.SPLIT for txn in txns):
        return None
    return sum((txn.units or ZERO for txn in txns), ZERO)


def _average_price(txns: Sequence[Txn]) -> Decimal | None:
    """Average the price over units of trades that are all buys or all sells."""
    if txns[0].action not in _PRICED_TRADES:
        return None
    priced = [
        (txn.price, abs(txn.units))
        for txn in txns
        if txn.price is not None and txn.units is not None
    ]
    total_units = sum((units for _, units in priced), ZERO)
    if total_units == 0:
        return None
    return sum((price * units for price, units in priced), ZERO) / total_units
