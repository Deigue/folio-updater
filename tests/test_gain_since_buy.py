"""Tests for the figures a report adds to the ledger when it runs."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from domain import Action, Currency, Grain, QuoteStatus
from engine.frames import grain_column, ledger_frame
from engine.fx_rates import FxRates
from engine.gain_since_buy import with_gains_since_buy
from models import Quote, QuoteSplit

from .helpers.replay import FLAT_FX, make_row, run

if TYPE_CHECKING:
    import pandas as pd

    from models import Txn

D = Decimal


def _quote(
    price: str,
    currency: Currency = Currency.CAD,
    splits: tuple[QuoteSplit, ...] | None = None,
) -> Quote:
    """Build a priced quote for the test security."""
    return Quote(
        symbol="TEST",
        provider_symbol="TEST",
        price=D(price),
        currency=currency,
        status=QuoteStatus.OK,
        splits=splits,
    )


def _valued(
    rows: list[Txn],
    quote: Quote | None,
    fx: FxRates = FLAT_FX,
) -> pd.DataFrame:
    """Replay rows and add what the report adds, indexed by TxnId."""
    frame = ledger_frame(run(rows, fx))
    return with_gains_since_buy(frame, {"TEST": quote} if quote else {}, fx)


def _buy(txn_id: int, date: str, account: str = "IBKR-PERSONAL") -> Txn:
    """Buy ten units at $100."""
    return make_row(
        txn_id,
        date,
        Action.BUY,
        amount="-1000",
        units="10",
        price="100",
        account=account,
    )


def _split(txn_id: int, date: str, account: str = "IBKR-PERSONAL") -> Txn:
    """Split two for one."""
    return make_row(txn_id, date, Action.SPLIT, units="2", price="1", account=account)


def test_a_buy_before_a_split_is_worth_the_units_it_became() -> None:
    frame = _valued([_buy(1, "2024-01-02"), _split(2, "2024-06-10")], _quote("60"))

    # 20 units at $60 is $1,200, against $1,000. Without the split: a false -40%.
    for grain in Grain:
        assert frame.loc[1, grain_column(grain, "Gain")] == pytest.approx(200)
        assert frame.loc[1, grain_column(grain, "GainPct")] == pytest.approx(0.2)
    # A CAD holding has no USD figures to give.
    assert frame["AcctGain_USD"].isna().loc[1]
    assert frame["AcctGainPct_USD"].isna().loc[1]


def test_a_split_counts_once_and_for_every_account() -> None:
    other, closed = "WS-PERSONAL", "QT-PERSONAL"
    frame = _valued(
        [
            _buy(1, "2024-01-02"),
            _buy(2, "2024-01-02", other),
            _buy(3, "2024-01-02", closed),
            make_row(
                4,
                "2024-02-01",
                Action.SELL,
                amount="1000",
                units="-10",
                price="100",
                account=closed,
            ),
            _split(5, "2024-06-10"),
            _split(6, "2024-06-10", other),
        ],
        _quote("60"),
    )

    # Two accounts reported it, and the third had sold out before it happened.
    assert frame.loc[1, "AcctGain"] == pytest.approx(200)
    assert frame.loc[3, "AcctGain"] == pytest.approx(200)


def test_provider_splits_fill_the_gaps_in_the_folio() -> None:
    splits = (
        QuoteSplit("2023-01-10", D(10)),  # before the buy
        QuoteSplit("2024-06-13", D(2)),  # the folio's own, dated days apart
        QuoteSplit("2024-06-24", D(5)),  # 14 days on: still the folio's
        QuoteSplit("2024-06-25", D(3)),  # 15 days on: one the folio never saw
    )
    frame = _valued(
        [
            _buy(1, "2024-01-02"),
            _split(2, "2024-06-10"),
            # Bought on the day the provider dates it, already at the new price.
            _buy(3, "2024-06-25"),
        ],
        _quote("100", splits=splits),
    )

    # 10 units became 60: $6,000 against $1,000.
    assert frame.loc[1, "AcctGain"] == pytest.approx(5000)
    assert frame.loc[3, "AcctGain"] == pytest.approx(0)


def test_a_usd_buy_gains_in_both_currencies_at_todays_rate() -> None:
    fx = FxRates(("2024-01-02", "2026-01-02"), (D("1.30"), D("1.40")))
    rows = [
        make_row(
            1,
            "2024-01-02",
            Action.BUY,
            amount="-1000",
            units="10",
            price="100",
            currency="USD",
        ),
    ]

    frame = _valued(rows, _quote("120", Currency.USD), fx)

    assert frame.loc[1, "AcctGain_USD"] == pytest.approx(200)
    assert frame.loc[1, "AcctGainPct_USD"] == pytest.approx(0.2)
    # Worth 1,680 CAD today, and cost 1,300 CAD.
    assert frame.loc[1, "AcctGain"] == pytest.approx(380)
    assert frame.loc[1, "AcctGainPct"] == pytest.approx(380 / 1300)


def test_gain_percent_on_rows_that_realized_a_gain_without_any_quote() -> None:
    frame = _valued(
        [
            _buy(1, "2024-01-02"),
            _buy(2, "2024-01-03", "WS-PERSONAL"),
            make_row(
                3,
                "2024-02-01",
                Action.SELL,
                amount="750",
                units="-5",
                price="150",
            ),
            # More than the 500 of cost left in this account.
            make_row(4, "2024-03-01", Action.ROC, amount="600"),
            # Nothing left in the other account's pool to take cost from.
            make_row(
                5,
                "2024-04-01",
                Action.SELL,
                amount="100",
                units="-1",
                price="100",
                account="QT-PERSONAL",
            ),
        ],
        None,
    )

    # No quote leaves a buy blank, never a made-up zero.
    assert frame["AcctGain"].isna().loc[1]
    assert frame["AcctGainPct"].isna().loc[1]
    # 750 of proceeds on 500 of cost removed.
    assert frame.loc[3, "AcctGainPct"] == pytest.approx(0.5)
    # The ROC earned 100, against no cost removed.
    assert frame.loc[4, "AcctGain"] == pytest.approx(100)
    assert frame["AcctGainPct"].isna().loc[4]
    assert frame.loc[5, "AcctGain"] == pytest.approx(100)
    assert frame["AcctGainPct"].isna().loc[5]
    # The type pool averages 100 a unit, so that first sale took 500 and earned 250.
    assert frame.loc[3, grain_column(Grain.TYPE, "GainPct")] == pytest.approx(0.5)


def test_the_ledger_it_was_given_stays_as_it_was() -> None:
    frame = ledger_frame(run([_buy(1, "2024-01-02")]))

    with_gains_since_buy(frame, {"TEST": _quote("120")}, FLAT_FX)

    assert frame["AcctGain"].isna().loc[1]
    assert "AcctGainPct" not in frame.columns
