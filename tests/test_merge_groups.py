"""Tests for transaction merge group functionality."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from contextlib import _GeneratorContextManager
    from typing import Callable

    from app.app_context import AppContext

    TempConfigType = Callable[..., _GeneratorContextManager[AppContext, None, None]]

from db.transformers import TransactionTransformer
from utils.constants import Column

MERGE_CONFIG = {
    "transforms": {
        "merge_groups": [
            {
                "name": "Dividend Withholding Tax Merge",
                "match_fields": ["TxnDate", "Account", "Ticker"],
                "source_actions": ["Dividends", "Withholding Tax"],
                "target_action": "DIVIDEND",
                "amount_field": "Amount",
                "operations": {"Fee": 0},
            },
        ],
        "rules": [
            {
                "conditions": {"Action": ["DIVIDEND"]},
                "actions": {"Units": "0"},
            },
        ],
    },
}


class TestMergeGroups:
    """Test merge group functionality."""

    def test_groups_with_transforms(self, temp_config: TempConfigType) -> None:
        """Test merging multiple dividend/tax pairs for different tickers."""
        merge_config = MERGE_CONFIG

        with temp_config(merge_config):
            df = pd.DataFrame(
                {
                    Column.Txn.TXN_DATE: [
                        "2025-09-10",
                        "2025-09-10",
                        "2025-09-10",
                        "2025-09-10",
                    ],
                    Column.Txn.ACTION: [
                        "Dividends",
                        "Withholding Tax",
                        "Dividends",
                        "Withholding Tax",
                    ],
                    Column.Txn.AMOUNT: [26.88, -4.03, 50.0, -7.5],
                    Column.Txn.CURRENCY: ["USD", "USD", "USD", "USD"],
                    Column.Txn.PRICE: [0.0, 0.0, 0.0, 0.0],
                    Column.Txn.UNITS: [1.0, 1.0, 1.0, 1.0],
                    Column.Txn.TICKER: ["AAPL", "AAPL", "SPY", "SPY"],
                    Column.Txn.ACCOUNT: [
                        "MOCK",
                        "MOCK",
                        "MOCK",
                        "MOCK",
                    ],
                    Column.Txn.FEE: [0.0, 0.0, 0.0, 0.0],
                },
            )

            result = TransactionTransformer.transform(df)
            expected_rows = 2
            assert len(result) == expected_rows

            # Check AAPL
            aapl_row = result[result[Column.Txn.TICKER] == "AAPL"].iloc[0]
            assert aapl_row[Column.Txn.ACTION] == "DIVIDEND"
            expected_aapl = Decimal("22.85")
            actual_aapl = Decimal(str(aapl_row[Column.Txn.AMOUNT]))
            assert actual_aapl == expected_aapl
            assert aapl_row[Column.Txn.UNITS] == 0  # From transform rule

            # Check SPY
            spy_row = result[result[Column.Txn.TICKER] == "SPY"].iloc[0]
            assert spy_row[Column.Txn.ACTION] == "DIVIDEND"
            expected_spy = Decimal("42.5")
            actual_spy = Decimal(str(spy_row[Column.Txn.AMOUNT]))
            assert actual_spy == expected_spy
            assert spy_row[Column.Txn.UNITS] == 0  # From transform rule

    def test_incomplete_pair_not_merged(self, temp_config: TempConfigType) -> None:
        """Test that rows without matching pairs are not merged."""
        merge_config = MERGE_CONFIG

        with temp_config(merge_config):
            # Only one dividend, no withholding tax
            df = pd.DataFrame(
                {
                    Column.Txn.TXN_DATE: ["2025-09-10"],
                    Column.Txn.ACTION: ["Dividends"],
                    Column.Txn.AMOUNT: [26.88],
                    Column.Txn.CURRENCY: ["USD"],
                    Column.Txn.PRICE: [0.0],
                    Column.Txn.UNITS: [1.0],
                    Column.Txn.TICKER: ["AAPL"],
                    Column.Txn.ACCOUNT: ["MOCK"],
                },
            )

            result = TransactionTransformer.transform(df)
            assert len(result) == 1
            assert result.iloc[0][Column.Txn.ACTION] == "Dividends"

    def test_different_dates_not_merged(self, temp_config: TempConfigType) -> None:
        """Test that transactions on different dates are not merged."""
        merge_config = MERGE_CONFIG

        with temp_config(merge_config):
            df = pd.DataFrame(
                {
                    Column.Txn.TXN_DATE: ["2025-09-10", "2025-09-11"],
                    Column.Txn.ACTION: ["Dividends", "Withholding Tax"],
                    Column.Txn.AMOUNT: [26.88, -4.03],
                    Column.Txn.CURRENCY: ["USD", "USD"],
                    Column.Txn.PRICE: [0.0, 0.0],
                    Column.Txn.UNITS: [1.0, 1.0],
                    Column.Txn.TICKER: ["AAPL", "AAPL"],
                    Column.Txn.ACCOUNT: ["MOCK", "MOCK"],
                },
            )

            result = TransactionTransformer.transform(df)
            assert len(result) == len(df)
