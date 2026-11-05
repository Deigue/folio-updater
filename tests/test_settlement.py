"""Tests for settlement dates and the trading calendar behind them."""

from __future__ import annotations

import sys

import pandas as pd
import pytest

from domain import TORONTO_TZ, Action, Column, Currency
from engine.settlement import SettlementCalculator, TradingCalendar


def _sessions(start: str, end: str, *, closed: tuple[str, ...]) -> list[str]:
    """List the weekdays in a range, leaving out the holidays named."""
    weekdays = pd.bdate_range(start, end).strftime("%Y-%m-%d")
    return [day for day in weekdays if day not in closed]


fixed_calendar = TradingCalendar(
    {
        Currency.USD: _sessions(
            "2024-05-21",
            "2025-08-29",
            closed=("2024-05-27", "2025-07-04"),
        ),
        Currency.CAD: _sessions(
            "2024-05-21",
            "2025-08-29",
            closed=("2025-07-01",),
        ),
    },
)
calculator = SettlementCalculator(fixed_calendar)
real_calculator = SettlementCalculator()

SETTLEMENT_DATES = [
    pytest.param("2025-07-03", Currency.USD, "2025-07-07", id="t1_over_us_holiday"),
    pytest.param("2025-07-03", Currency.CAD, "2025-07-04", id="t1_us_holiday_only"),
    pytest.param("2025-06-30", Currency.CAD, "2025-07-02", id="t1_over_cad_holiday"),
    pytest.param("2024-05-24", Currency.USD, "2024-05-29", id="last_usd_t2"),
    pytest.param("2024-05-24", Currency.CAD, "2024-05-28", id="last_cad_t2"),
    pytest.param("2024-05-27", Currency.CAD, "2024-05-28", id="first_cad_t1"),
    pytest.param("2024-05-28", Currency.USD, "2024-05-29", id="first_usd_t1"),
]


def _settle(
    settlement: SettlementCalculator,
    txn_date: str,
    currency: Currency,
) -> str:
    """Settle one buy and return its settlement date."""
    df = pd.DataFrame(
        {
            Column.Txn.TXN_DATE: [txn_date],
            Column.Txn.ACTION: [Action.BUY],
            Column.Txn.CURRENCY: [currency],
        },
    )
    result = settlement.add_settlement_dates_to_dataframe(df)
    return str(result.loc[0, Column.Txn.SETTLE_DATE])


@pytest.mark.parametrize(("txn_date", "currency", "expected"), SETTLEMENT_DATES)
def test_a_buy_settles_by_its_exchange_and_the_settlement_lag(
    txn_date: str,
    currency: Currency,
    expected: str,
) -> None:
    assert _settle(calculator, txn_date, currency) == expected


@pytest.mark.parametrize(("txn_date", "currency", "expected"), SETTLEMENT_DATES)
def test_the_real_calendar_gives_the_same_settlement_dates(
    txn_date: str,
    currency: Currency,
    expected: str,
) -> None:
    assert _settle(real_calculator, txn_date, currency) == expected


def test_creating_a_calculator_does_not_load_the_calendar_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commands that never settle anything should not pay for the library."""
    monkeypatch.setitem(sys.modules, "pandas_market_calendars", None)

    SettlementCalculator()


def test_an_empty_frame_comes_back_empty() -> None:
    assert calculator.add_settlement_dates_to_dataframe(pd.DataFrame()).empty


@pytest.mark.parametrize(
    (
        "txn_date",
        "action",
        "currency",
        "settle_date",
        "expected_settle",
        "expected_calculated",
    ),
    [
        pytest.param(
            "2025-07-24",
            Action.BUY,
            Currency.USD,
            "2025-07-29",
            "2025-07-29",
            0,
            id="valid_existing_is_kept",
        ),
        pytest.param(
            "2025-07-24",
            Action.BUY,
            Currency.USD,
            "invalid-date",
            "2025-07-25",
            1,
            id="invalid_existing_is_recalculated",
        ),
        pytest.param(
            "2025-07-24",
            Action.DIVIDEND,
            Currency.USD,
            None,
            "2025-07-24",
            0,
            id="same_day_action_is_not_calculated",
        ),
        pytest.param(
            "2025-08-05",
            Action.BUY,
            Currency.USD,
            None,
            "2025-08-06",
            1,
            id="business_day_action_is_calculated",
        ),
        pytest.param(
            None,
            Action.BUY,
            Currency.USD,
            None,
            pd.NA,
            0,
            id="missing_transaction_date_does_not_crash",
        ),
        pytest.param(
            "2025-08-18",
            "BUY",
            "EUR",
            None,
            "2025-08-20",
            1,
            id="nonstandard_currency",
        ),
        pytest.param(
            "2025-08-18",
            "BUY",
            "GBP",
            None,
            "2025-08-20",
            1,
            id="unrecognized_currency",
        ),
    ],
)
def test_settlement_scenarios(
    txn_date: str | None,
    action: str,
    currency: str,
    settle_date: str | None,
    expected_settle: str | None,
    expected_calculated: int,
) -> None:
    """Test various settlement date calculation scenarios."""
    df = pd.DataFrame(
        {
            Column.Txn.TXN_DATE: [txn_date if txn_date is not None else pd.NA],
            Column.Txn.ACTION: [action],
            Column.Txn.CURRENCY: [currency],
        },
    )

    if settle_date is not None:
        df[Column.Txn.SETTLE_DATE] = [settle_date]

    result = calculator.add_settlement_dates_to_dataframe(df)

    if expected_settle is pd.NA:
        assert pd.isna(result.loc[0, Column.Txn.SETTLE_DATE])
    else:
        assert result.loc[0, Column.Txn.SETTLE_DATE] == expected_settle

    assert result.loc[0, Column.Txn.SETTLE_CALCULATED] == expected_calculated


def test_multiple_txns() -> None:
    """Test processing multiple transactions with mixed scenarios."""
    df = pd.DataFrame(
        {
            Column.Txn.TXN_DATE: ["2025-07-24", "2025-07-24", "2025-07-24"],
            Column.Txn.ACTION: [Action.BUY, Action.DIVIDEND, Action.DIVIDEND],
            Column.Txn.CURRENCY: [Currency.USD, Currency.USD, Currency.CAD],
            Column.Txn.SETTLE_DATE: [pd.NA, "2025-07-29", pd.NA],
        },
    )

    result = calculator.add_settlement_dates_to_dataframe(df)

    # BUY, no existing date -> T+1
    assert result.loc[0, Column.Txn.SETTLE_DATE] == "2025-07-25"
    assert result.loc[0, Column.Txn.SETTLE_CALCULATED] == 1

    # DIVIDEND, existing date -> preserved
    assert result.loc[1, Column.Txn.SETTLE_DATE] == "2025-07-29"
    assert result.loc[1, Column.Txn.SETTLE_CALCULATED] == 0

    # DIVIDEND, no existing date -> same day as transaction date
    assert result.loc[2, Column.Txn.SETTLE_DATE] == "2025-07-24"
    assert result.loc[2, Column.Txn.SETTLE_CALCULATED] == 0


@pytest.mark.parametrize(
    ("start_date", "days", "expected"),
    [
        ("2025-07-24", 1, "2025-07-25"),
        ("2025-07-24", 2, "2025-07-28"),
        ("2025-08-05", 1, "2025-08-06"),
        ("2025-08-18", 3, "2025-08-21"),
    ],
)
def test_a_currency_with_no_exchange_counts_plain_weekdays(
    start_date: str,
    days: int,
    expected: str,
) -> None:
    result = TradingCalendar().nth_trading_day_after(
        pd.Timestamp(start_date),
        days,
        Currency.EUR,
    )
    assert result == expected


def test_the_trading_day_lookup_loads_what_it_needs() -> None:
    """Nothing loaded yet still means real trading days: Jul 4 is skipped."""
    settles = TradingCalendar().nth_trading_day_after(
        pd.Timestamp("2025-07-03"),
        1,
        Currency.USD,
    )
    assert settles == "2025-07-07"


def test_an_earlier_batch_widens_a_cached_calendar() -> None:
    """A transaction dated before the cached schedule still settles on the next day.

    The cache used to be trusted once loaded, so in one process an older
    batch following a newer one settled against the wrong trading days, and
    could even settle before it traded.
    """
    calendar = TradingCalendar()
    newer_start = pd.Timestamp("2025-06-01", tz=TORONTO_TZ)
    newer_end = pd.Timestamp("2025-10-01", tz=TORONTO_TZ)
    calendar.trading_days(Currency.USD, newer_start, newer_end)
    df = pd.DataFrame(
        {
            Column.Txn.TXN_DATE: ["2024-11-06", "2025-07-24"],
            Column.Txn.ACTION: [Action.BUY, Action.BUY],
            Column.Txn.CURRENCY: [Currency.USD, Currency.USD],
        },
    )

    result = SettlementCalculator(calendar).add_settlement_dates_to_dataframe(df)

    assert result[Column.Txn.SETTLE_DATE].tolist() == ["2024-11-07", "2025-07-25"]
    # The days already loaded are kept, not swapped for the older range.
    kept = calendar.trading_days(Currency.USD, newer_start, newer_end)
    assert kept[-1] >= pd.Timestamp("2025-10-01")
