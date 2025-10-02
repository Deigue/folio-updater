"""Tests for settlement date calculation functionality."""

from __future__ import annotations

import pandas as pd

from utils.constants import Action, Column, Currency
from utils.settlement_calculator import SettlementCalculator


class TestSettlementCalculator:
    """Tests for the SettlementCalculator class."""

    def setup_method(self) -> None:
        """Set up test fixtures."""
        self.calculator = SettlementCalculator()

    def test_add_settlement_dates_to_dataframe_empty(self) -> None:
        """Test that empty dataframes are handled correctly."""
        empty_df = pd.DataFrame()
        result = self.calculator.add_settlement_dates_to_dataframe(empty_df)
        assert result.empty

    def test_add_settlement_dates_to_dataframe_with_existing_valid_date(self) -> None:
        """Test that existing valid settlement dates are preserved."""
        df = pd.DataFrame(
            {
                Column.Txn.TXN_DATE.value: ["2024-06-03"],
                Column.Txn.ACTION.value: [Action.BUY.value],
                Column.Txn.CURRENCY.value: [Currency.USD.value],
                Column.Txn.SETTLE_DATE.value: ["2024-06-05"],  # Existing valid date
            },
        )

        result = self.calculator.add_settlement_dates_to_dataframe(df)

        assert result.loc[0, Column.Txn.SETTLE_DATE.value] == "2024-06-05"
        assert result.loc[0, Column.Txn.SETTLE_CALCULATED.value] == 0  # Not calculated

    def test_add_settlement_dates_to_dataframe_with_invalid_date(self) -> None:
        """Test that invalid settlement dates are recalculated."""
        df = pd.DataFrame(
            {
                Column.Txn.TXN_DATE.value: ["2024-06-03"],
                Column.Txn.ACTION.value: [Action.BUY.value],
                Column.Txn.CURRENCY.value: [Currency.USD.value],
                Column.Txn.SETTLE_DATE.value: ["invalid-date"],  # Invalid date
            },
        )

        result = self.calculator.add_settlement_dates_to_dataframe(df)

        # Should be calculated to T+1 from transaction date
        assert result.loc[0, Column.Txn.SETTLE_DATE.value] == "2024-06-04"
        assert result.loc[0, Column.Txn.SETTLE_CALCULATED.value] == 1  # Calculated

    def test_add_settlement_dates_to_dataframe_no_existing_date(self) -> None:
        """Test calculation when no settlement date exists."""
        df = pd.DataFrame(
            {
                Column.Txn.TXN_DATE.value: ["2024-06-03"],
                Column.Txn.ACTION.value: [Action.DIVIDEND.value],
                Column.Txn.CURRENCY.value: [Currency.USD.value],
            },
        )

        result = self.calculator.add_settlement_dates_to_dataframe(df)

        # Dividend should settle same day
        assert result.loc[0, Column.Txn.SETTLE_DATE.value] == "2024-06-03"
        assert result.loc[0, Column.Txn.SETTLE_CALCULATED.value] == 1  # Calculated

    def test_add_settlement_dates_to_dataframe_missing_required_data(self) -> None:
        """Test handling of missing required data."""
        df = pd.DataFrame(
            {
                Column.Txn.TXN_DATE.value: [pd.NA],  # Missing transaction date
                Column.Txn.ACTION.value: [Action.BUY.value],
                Column.Txn.CURRENCY.value: [Currency.USD.value],
            },
        )

        result = self.calculator.add_settlement_dates_to_dataframe(df)

        # Should not crash, but settlement date should remain unset
        assert pd.isna(result.loc[0, Column.Txn.SETTLE_DATE.value])
        assert result.loc[0, Column.Txn.SETTLE_CALCULATED.value] == 0

    def test_add_settlement_dates_to_dataframe_nonstandard_currency(self) -> None:
        """Test handling of a non-standard currency."""
        # Create a DataFrame with unsupported currency
        df = pd.DataFrame(
            {
                Column.Txn.TXN_DATE.value: ["2025-10-02"],
                Column.Txn.ACTION.value: ["BUY"],
                Column.Txn.CURRENCY.value: ["EUR"],
                Column.Txn.SETTLE_DATE.value: [None],
                Column.Txn.SETTLE_CALCULATED.value: [0],
            },
        )

        result = self.calculator.add_settlement_dates_to_dataframe(df)

        # Should add a settlement date, but fallback to simple business day logic
        assert result.loc[0, Column.Txn.SETTLE_DATE.value] == "2025-10-02"
        assert result.loc[0, Column.Txn.SETTLE_CALCULATED.value] == 1  # Calculated

    def test_multiple_transactions_dataframe(self) -> None:
        """Test processing multiple transactions with mixed scenarios."""
        df = pd.DataFrame(
            {
                Column.Txn.TXN_DATE.value: [
                    "2024-06-03",  # Monday
                    "2024-06-03",  # Monday
                    "2024-06-03",  # Monday
                ],
                Column.Txn.ACTION.value: [
                    Action.BUY.value,  # Should be T+1
                    Action.DIVIDEND.value,  # Should be same day
                    Action.SELL.value,  # Should be T+1
                ],
                Column.Txn.CURRENCY.value: [
                    Currency.USD.value,
                    Currency.USD.value,
                    Currency.CAD.value,
                ],
                Column.Txn.SETTLE_DATE.value: [
                    pd.NA,  # Will be calculated
                    "2024-06-05",  # Existing valid date
                    pd.NA,  # Will be calculated
                ],
            },
        )

        result = self.calculator.add_settlement_dates_to_dataframe(df)

        # Check first transaction (BUY, no existing date)
        assert result.loc[0, Column.Txn.SETTLE_DATE.value] == "2024-06-04"  # T+1
        assert result.loc[0, Column.Txn.SETTLE_CALCULATED.value] == 1

        # Check second transaction (DIVIDEND, existing date)
        assert result.loc[1, Column.Txn.SETTLE_DATE.value] == "2024-06-05"  # Preserved
        assert result.loc[1, Column.Txn.SETTLE_CALCULATED.value] == 0

        # Check third transaction (SELL, no existing date)
        assert result.loc[2, Column.Txn.SETTLE_DATE.value] == "2024-06-04"  # T+1
        assert result.loc[2, Column.Txn.SETTLE_CALCULATED.value] == 1
