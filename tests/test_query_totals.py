"""Tests for the totals under a `folio query` result."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from domain import Action, Currency, FeeConvention, FeeSign
from engine.fees import FeeRules
from engine.query_totals import query_totals, resolved_fees

from .helpers.replay import make_row

if TYPE_CHECKING:
    from models import Txn
    from models.query_totals import QueryTotals

    from .helpers.context import TempContext

_NEGATIVE_FEES = {"ALPHA-TFSA": FeeRules(FeeConvention.EXCLUDED, FeeSign.NEGATIVE)}
_POSITIVE_FEES = {"BETA-RRSP": FeeRules(FeeConvention.EXCLUDED, FeeSign.POSITIVE)}


def _totals(txns: list[Txn]) -> QueryTotals:
    """Add up transactions that carry no fee, which a query never leaves empty."""
    totals = query_totals(txns, {})
    assert totals is not None
    return totals


def test_nothing_matched_has_no_totals() -> None:
    assert query_totals([], {}) is None


def test_sums_stay_per_currency_and_commissions_follow_each_accounts_sign() -> None:
    txns = [
        make_row(
            1,
            "2024-02-05",
            Action.BUY,
            amount="-812.40",
            units="6",
            price="135.40",
            fee="-1.25",
            account="ALPHA-TFSA",
            ticker="SPY",
            currency="USD",
        ),
        make_row(
            2,
            "2024-03-11",
            Action.SELL,
            amount="391.70",
            units="-3",
            price="130.98",
            fee="2.05",
            account="BETA-RRSP",
            ticker="RY.TO",
        ),
        make_row(
            3,
            "2024-03-28",
            Action.DIVIDEND,
            amount="17.36",
            account="BETA-RRSP",
            ticker="RY.TO",
        ),
        make_row(
            4,
            "2024-04-02",
            Action.FCH,
            amount="0.42",
            account="ALPHA-TFSA",
            ticker=None,
            currency="USD",
        ),
    ]

    totals = query_totals(txns, {**_NEGATIVE_FEES, **_POSITIVE_FEES})

    assert totals is not None
    assert (totals.count, totals.first_date, totals.last_date) == (
        4,
        "2024-02-05",
        "2024-04-02",
    )
    usd, cad = totals.by_currency
    assert (usd.currency, usd.net_amount, usd.commissions) == (
        Currency.USD,
        Decimal("-811.98"),
        Decimal("1.25"),
    )
    assert (cad.currency, cad.net_amount, cad.commissions) == (
        Currency.CAD,
        Decimal("409.06"),
        Decimal("2.05"),
    )
    # Mixed actions and tickers: no averages, no units.
    assert usd.average_amount is None
    assert cad.per_month is None
    assert totals.units is None
    assert totals.average_price is None


def test_one_action_gets_an_average_and_a_monthly_figure() -> None:
    txns = [
        make_row(1, "2023-11-20", Action.DIVIDEND, amount="12.30", ticker="VFV.TO"),
        make_row(2, "2024-01-19", Action.DIVIDEND, amount="13.10", ticker="XEQT.TO"),
        make_row(3, "2024-02-16", Action.DIVIDEND, amount="14.60", ticker="VFV.TO"),
    ]

    (cad,) = _totals(txns).by_currency

    assert cad.average_amount == Decimal("40.00") / 3
    # November to February is four calendar months.
    assert cad.per_month == Decimal("10.00")


def test_buys_of_one_ticker_get_net_units_and_a_unit_weighted_price() -> None:
    txns = [
        make_row(
            1,
            "2024-05-06",
            Action.BUY,
            amount="-410.00",
            units="4",
            price="102.50",
            ticker="MSFT",
        ),
        make_row(
            2,
            "2024-06-10",
            Action.BUY,
            amount="-1110.00",
            units="10",
            price="111.00",
            ticker="MSFT",
        ),
    ]

    totals = _totals(txns)

    assert totals.units == Decimal(14)
    assert totals.average_price == Decimal("1520.00") / 14


def test_buys_and_sells_of_one_ticker_net_their_units_without_a_price() -> None:
    txns = [
        make_row(
            1,
            "2024-05-06",
            Action.BUY,
            amount="-500.00",
            units="5",
            price="100",
            ticker="NFLX",
        ),
        make_row(
            2,
            "2024-07-08",
            Action.SELL,
            amount="220.00",
            units="-2",
            price="110",
            ticker="NFLX",
        ),
    ]

    totals = _totals(txns)

    assert totals.units == Decimal(3)
    assert totals.average_price is None


def test_a_split_leaves_units_out() -> None:
    txns = [
        make_row(
            1,
            "2024-05-06",
            Action.BUY,
            amount="-500.00",
            units="5",
            price="100",
            ticker="NFLX",
        ),
        make_row(2, "2024-06-03", Action.SPLIT, units="10", price="5", ticker="NFLX"),
    ]

    assert _totals(txns).units is None


def test_dividends_of_one_ticker_have_no_average_price() -> None:
    txns = [
        make_row(1, "2024-03-01", Action.DIVIDEND, amount="8.20", ticker="RY.TO"),
        make_row(2, "2024-06-01", Action.DIVIDEND, amount="8.40", ticker="RY.TO"),
    ]

    assert _totals(txns).average_price is None


def test_trades_without_units_have_no_average_price() -> None:
    txns = [make_row(1, "2024-03-01", Action.BUY, amount="-90.00", ticker="SPY")]

    assert _totals(txns).average_price is None


def _bought_with_fee(txn_id: int, account: str, fee: str) -> Txn:
    """Build one buy whose fee carries the sign a test wants."""
    return make_row(
        txn_id,
        "2024-02-12",
        Action.BUY,
        amount="-202.00",
        units="2",
        price="100",
        fee=fee,
        account=account,
    )


def test_fee_rules_left_on_auto_are_settled_from_the_accounts_own_rows(
    temp_ctx: TempContext,
) -> None:
    # Pooled together, the positive fees would outvote the negative ones.
    stored = [
        _bought_with_fee(1, "ALPHA-TFSA", "-1.00"),
        _bought_with_fee(2, "ALPHA-TFSA", "-2.00"),
        *(_bought_with_fee(n, "BETA-TFSA", "3.00") for n in (3, 4, 5)),
    ]

    with temp_ctx():
        fees = resolved_fees(["ALPHA-TFSA"], stored)

    assert list(fees) == ["ALPHA-TFSA"]
    assert fees["ALPHA-TFSA"].sign is FeeSign.NEGATIVE
