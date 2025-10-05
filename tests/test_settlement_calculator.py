"""Optimized tests for settlement calculator - concise and comprehensive."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from utils.constants import TORONTO_TZ, Action, Column, Currency
from utils.settlement_calculator import SettlementCalculator

# Shared calculator instance
calculator = SettlementCalculator()


@pytest.mark.parametrize(
    (
        "scenario",
        "txn_date",
        "action",
        "currency",
        "settle_date",
        "expected_settle",
        "expected_calculated",
    ),
    [
        # Empty dataframe - special case
        ("empty", None, None, None, None, None, None),
        # Existing valid settlement date - preserve it
        (
            "valid_existing",
            "2024-06-03",
            Action.BUY,
            Currency.USD,
            "2024-06-05",
            "2024-06-05",
            0,
        ),
        # Invalid settlement date - recalculate
        (
            "invalid_existing",
            "2024-06-03",
            Action.BUY,
            Currency.USD,
            "invalid-date",
            "2024-06-04",
            1,
        ),
        # No existing date - calculate
        (
            "no_existing",
            "2024-06-03",
            Action.DIVIDEND,
            Currency.USD,
            None,
            "2024-06-03",
            1,
        ),
        # Missing required data - should not crash
        ("missing_data", None, Action.BUY, Currency.USD, None, pd.NA, 0),
        # Non-standard currency (EUR)
        (
            "nonstandard_currency",
            "2025-10-02",
            "BUY",
            "EUR",
            None,
            "2025-10-06",
            1,
        ),
    ],
)
def test_settlement_scenarios(  # noqa: PLR0913
    scenario: str,
    txn_date: str | None,
    action: str | None,
    currency: str | None,
    settle_date: str | None,
    expected_settle: str | None,
    expected_calculated: int | None,
) -> None:
    """Test various settlement date calculation scenarios."""
    if scenario == "empty":
        result = calculator.add_settlement_dates_to_dataframe(pd.DataFrame())
        assert result.empty
        return

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
            Column.Txn.TXN_DATE: ["2024-06-03", "2024-06-03", "2024-06-03"],
            Column.Txn.ACTION: [Action.BUY, Action.DIVIDEND, Action.SELL],
            Column.Txn.CURRENCY: [Currency.USD, Currency.USD, Currency.CAD],
            Column.Txn.SETTLE_DATE: [pd.NA, "2024-06-05", pd.NA],
        },
    )

    result = calculator.add_settlement_dates_to_dataframe(df)

    # BUY, no existing date -> T+1
    assert result.loc[0, Column.Txn.SETTLE_DATE] == "2024-06-04"
    assert result.loc[0, Column.Txn.SETTLE_CALCULATED] == 1

    # DIVIDEND, existing date -> preserved
    assert result.loc[1, Column.Txn.SETTLE_DATE] == "2024-06-05"
    assert result.loc[1, Column.Txn.SETTLE_CALCULATED] == 0

    # SELL, no existing date -> T+1
    assert result.loc[2, Column.Txn.SETTLE_DATE] == "2024-06-04"
    assert result.loc[2, Column.Txn.SETTLE_CALCULATED] == 1


@pytest.mark.parametrize(
    ("start_date", "days", "expected"),
    [
        ("2025-10-06", 1, "2025-10-07"),
        ("2025-10-06", 2, "2025-10-08"),
        ("2025-10-10", 1, "2025-10-13"),
        ("2025-10-10", 3, "2025-10-15"),
    ],
)
def test_business_days(start_date: str, days: int, expected: str) -> None:
    """Test simple business day calculation."""
    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=TORONTO_TZ).date()
    result = calculator.calculate_simple_business_days(start, days)
    assert result == expected
