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


class TestMergeGroups:
    """Test merge group functionality."""

    def test_dividend_withholding_tax_merge(
        self,
        temp_config: TempConfigType,
    ) -> None:
        """Test simple dividend and withholding tax merge.."""
        merge_config = {
            "transforms": {
                "merge_groups": [
                    {
                        "name": "Dividend Withholding Tax Merge",
                        "match_fields": ["TxnDate", "Account", "Ticker"],
                        "source_actions": ["Dividends", "Withholding Tax"],
                        "target_action": "DIVIDEND",
                        "amount_field": "Amount",
                        "operations": {"Fee": 0, "Units": 0},
                    },
                ],
            },
        }

        with temp_config(merge_config):
            df = pd.DataFrame(
                {
                    Column.Txn.TXN_DATE.value: ["2025-09-10", "2025-09-10"],
                    Column.Txn.ACTION.value: ["Dividends", "Withholding Tax"],
                    Column.Txn.AMOUNT.value: [26.88, -4.03],
                    Column.Txn.CURRENCY.value: ["USD", "USD"],
                    Column.Txn.PRICE.value: [0.0, 0.0],
                    Column.Txn.UNITS.value: [1.0, 1.0],
                    Column.Txn.TICKER.value: ["AAPL", "AAPL"],
                    Column.Txn.ACCOUNT.value: ["MOCK", "MOCK"],
                    Column.Txn.FEE.value: [0.0, 0.0],
                },
            )

            result = TransactionTransformer.transform(df)
            assert len(result) == 1
            merged_row = result.iloc[0]
            assert merged_row[Column.Txn.ACTION.value] == "DIVIDEND"
            expected_amount = Decimal("22.85")
            actual_amount = Decimal(str(merged_row[Column.Txn.AMOUNT.value]))
            assert actual_amount == expected_amount
            assert merged_row[Column.Txn.TICKER.value] == "AAPL"
            assert merged_row[Column.Txn.ACCOUNT.value] == "MOCK"
            assert merged_row[Column.Txn.FEE.value] == 0
            assert merged_row[Column.Txn.UNITS.value] == 0

    def test_multiple_dividend_groups(self, temp_config: TempConfigType) -> None:
        """Test merging multiple dividend/tax pairs for different tickers."""
        merge_config = {
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
            },
        }

        with temp_config(merge_config):
            df = pd.DataFrame(
                {
                    Column.Txn.TXN_DATE.value: [
                        "2025-09-10",
                        "2025-09-10",
                        "2025-09-10",
                        "2025-09-10",
                    ],
                    Column.Txn.ACTION.value: [
                        "Dividends",
                        "Withholding Tax",
                        "Dividends",
                        "Withholding Tax",
                    ],
                    Column.Txn.AMOUNT.value: [26.88, -4.03, 50.0, -7.5],
                    Column.Txn.CURRENCY.value: ["USD", "USD", "USD", "USD"],
                    Column.Txn.PRICE.value: [0.0, 0.0, 0.0, 0.0],
                    Column.Txn.UNITS.value: [1.0, 1.0, 1.0, 1.0],
                    Column.Txn.TICKER.value: ["AAPL", "AAPL", "SPY", "SPY"],
                    Column.Txn.ACCOUNT.value: [
                        "MOCK",
                        "MOCK",
                        "MOCK",
                        "MOCK",
                    ],
                    Column.Txn.FEE.value: [0.0, 0.0, 0.0, 0.0],
                },
            )

            result = TransactionTransformer.transform(df)
            expected_rows = 2
            assert len(result) == expected_rows

            # Check AAPL
            aapl_row = result[result[Column.Txn.TICKER.value] == "AAPL"].iloc[0]
            assert aapl_row[Column.Txn.ACTION.value] == "DIVIDEND"
            expected_aapl = Decimal("22.85")
            actual_aapl = Decimal(str(aapl_row[Column.Txn.AMOUNT.value]))
            assert actual_aapl == expected_aapl

            # Check SPY
            spy_row = result[result[Column.Txn.TICKER.value] == "SPY"].iloc[0]
            assert spy_row[Column.Txn.ACTION.value] == "DIVIDEND"
            expected_spy = Decimal("42.5")
            actual_spy = Decimal(str(spy_row[Column.Txn.AMOUNT.value]))
            assert actual_spy == expected_spy

    def test_incomplete_pair_not_merged(self, temp_config: TempConfigType) -> None:
        """Test that rows without matching pairs are not merged."""
        merge_config = {
            "transforms": {
                "merge_groups": [
                    {
                        "name": "Dividend Withholding Tax Merge",
                        "match_fields": ["TxnDate", "Account", "Ticker"],
                        "source_actions": ["Dividends", "Withholding Tax"],
                        "target_action": "DIVIDEND",
                        "amount_field": "Amount",
                        "operations": {},
                    },
                ],
            },
        }

        with temp_config(merge_config):
            # Only one dividend, no withholding tax
            df = pd.DataFrame(
                {
                    Column.Txn.TXN_DATE.value: ["2025-09-10"],
                    Column.Txn.ACTION.value: ["Dividends"],
                    Column.Txn.AMOUNT.value: [26.88],
                    Column.Txn.CURRENCY.value: ["USD"],
                    Column.Txn.PRICE.value: [0.0],
                    Column.Txn.UNITS.value: [1.0],
                    Column.Txn.TICKER.value: ["AAPL"],
                    Column.Txn.ACCOUNT.value: ["MOCK"],
                },
            )

            result = TransactionTransformer.transform(df)
            assert len(result) == 1
            assert result.iloc[0][Column.Txn.ACTION.value] == "Dividends"

    def test_different_dates_not_merged(self, temp_config: TempConfigType) -> None:
        """Test that transactions on different dates are not merged."""
        merge_config = {
            "transforms": {
                "merge_groups": [
                    {
                        "name": "Dividend Withholding Tax Merge",
                        "match_fields": ["TxnDate", "Account", "Ticker"],
                        "source_actions": ["Dividends", "Withholding Tax"],
                        "target_action": "DIVIDEND",
                        "amount_field": "Amount",
                        "operations": {},
                    },
                ],
            },
        }

        with temp_config(merge_config):
            df = pd.DataFrame(
                {
                    Column.Txn.TXN_DATE.value: ["2025-09-10", "2025-09-11"],
                    Column.Txn.ACTION.value: ["Dividends", "Withholding Tax"],
                    Column.Txn.AMOUNT.value: [26.88, -4.03],
                    Column.Txn.CURRENCY.value: ["USD", "USD"],
                    Column.Txn.PRICE.value: [0.0, 0.0],
                    Column.Txn.UNITS.value: [1.0, 1.0],
                    Column.Txn.TICKER.value: ["AAPL", "AAPL"],
                    Column.Txn.ACCOUNT.value: ["MOCK", "MOCK"],
                },
            )

            result = TransactionTransformer.transform(df)
            assert len(result) == len(df)

    def test_merge_with_transforms(self, temp_config: TempConfigType) -> None:
        """Test that merge groups work with regular transformation rules."""
        config = {
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

        with temp_config(config):
            df = pd.DataFrame(
                {
                    Column.Txn.TXN_DATE.value: ["2025-09-10", "2025-09-10"],
                    Column.Txn.ACTION.value: ["Dividends", "Withholding Tax"],
                    Column.Txn.AMOUNT.value: [26.88, -4.03],
                    Column.Txn.CURRENCY.value: ["USD", "USD"],
                    Column.Txn.PRICE.value: [0.0, 0.0],
                    Column.Txn.UNITS.value: [1.0, 1.0],
                    Column.Txn.TICKER.value: ["AAPL", "AAPL"],
                    Column.Txn.ACCOUNT.value: ["MOCK", "MOCK"],
                    Column.Txn.FEE.value: [0.0, 0.0],
                },
            )

            result = TransactionTransformer.transform(df)

            # Merge should happen first, then rule should apply
            assert len(result) == 1
            merged_row = result.iloc[0]
            assert merged_row[Column.Txn.ACTION.value] == "DIVIDEND"
            assert merged_row[Column.Txn.FEE.value] == 0  # From merge operations
            assert merged_row[Column.Txn.UNITS.value] == 0  # From transform rule

    def test_no_merge_groups_configured(self, temp_config: TempConfigType) -> None:
        """Test that system works normally when no merge groups configured."""
        with temp_config():
            df = pd.DataFrame(
                {
                    Column.Txn.TXN_DATE.value: ["2025-09-10"],
                    Column.Txn.ACTION.value: ["BUY"],
                    Column.Txn.AMOUNT.value: [1000.0],
                    Column.Txn.CURRENCY.value: ["USD"],
                    Column.Txn.PRICE.value: [100.0],
                    Column.Txn.UNITS.value: [10.0],
                    Column.Txn.TICKER.value: ["AAPL"],
                    Column.Txn.ACCOUNT.value: ["TEST"],
                },
            )

            result = TransactionTransformer.transform(df)
            pd.testing.assert_frame_equal(result, df)
