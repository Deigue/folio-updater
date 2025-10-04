"""Tests for transaction transformation functionality."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from contextlib import _GeneratorContextManager
    from typing import Callable

    from app.app_context import AppContext

    TempConfigType = Callable[..., _GeneratorContextManager[AppContext, None, None]]
from db.transformers import TransactionTransformer
from utils.constants import Column

# Test constants
EXPECTED_RULES_COUNT = 2
FEE_AMOUNT = 9.95


class TestTransactionTransformer:
    """Test TransactionTransformer class."""

    def test_empty_dataframe(self) -> None:
        """Test transformer with empty DataFrame."""
        df = pd.DataFrame()
        result = TransactionTransformer.transform(df)
        assert result.empty

    def test_no_transform_rules(self, temp_config: TempConfigType) -> None:
        """Test transformer with no transformation rules configured."""
        with temp_config():
            df = pd.DataFrame(
                {
                    Column.Txn.ACTION: ["BUY"],
                    Column.Txn.TICKER: ["AAPL"],
                },
            )
            result = TransactionTransformer.transform(df)
            pd.testing.assert_frame_equal(result, df)

    def test_fx_transformation(self, temp_config: TempConfigType) -> None:
        """Test FX transformation for USD.CAD trades."""
        transforms_config = {
            "transforms": {
                "rules": [
                    {
                        "conditions": {
                            "Action": ["BUY", "SELL"],
                            "Ticker": ["USD.CAD"],
                        },
                        "actions": {
                            "Action": "FXT",
                            "Ticker": "",
                        },
                    },
                ],
            },
        }

        with temp_config(transforms_config):
            df = pd.DataFrame(
                {
                    Column.Txn.ACTION: ["BUY", "SELL", "BUY"],
                    Column.Txn.TICKER: ["USD.CAD", "USD.CAD", "AAPL"],
                    Column.Txn.AMOUNT: [1000, 1000, 500],
                },
            )

            result = TransactionTransformer.transform(df)

            # First two rows should be transformed to FXT with empty ticker
            assert result.iloc[0][Column.Txn.ACTION] == "FXT"
            assert pd.isna(result.iloc[0][Column.Txn.TICKER])
            assert result.iloc[1][Column.Txn.ACTION] == "FXT"
            assert pd.isna(result.iloc[1][Column.Txn.TICKER])

            # Third row should remain unchanged
            assert result.iloc[2][Column.Txn.ACTION] == "BUY"
            assert result.iloc[2][Column.Txn.TICKER] == "AAPL"

    def test_dividend_fee_transform(
        self,
        temp_config: TempConfigType,
    ) -> None:
        """Test setting commission to 0 for dividend transactions."""
        transforms_config = {
            "transforms": {
                "rules": [
                    {
                        "conditions": {"Action": ["DIVIDEND"]},
                        "actions": {"Fee": "0"},
                    },
                ],
            },
        }

        with temp_config(transforms_config):
            df = pd.DataFrame(
                {
                    Column.Txn.ACTION: ["DIVIDEND", "BUY"],
                    Column.Txn.TICKER: ["AAPL", "MSFT"],
                    "Fee": [5.0, 9.95],
                },
            )

            result = TransactionTransformer.transform(df)

            # Dividend row should have fee set to 0 (autorecognize dtype)
            assert result.iloc[0]["Fee"] == 0
            # Buy row should remain unchanged
            assert result.iloc[1]["Fee"] == FEE_AMOUNT

    def test_multiple_transformations(self, temp_config: TempConfigType) -> None:
        """Test applying multiple transformation rules."""
        transforms_config = {
            "transforms": {
                "rules": [
                    {
                        "conditions": {
                            "Action": ["BUY", "SELL"],
                            "Ticker": ["USD.CAD"],
                        },
                        "actions": {"Action": "FXT", "Ticker": ""},
                    },
                    {
                        "conditions": {"Action": ["DIVIDEND"]},
                        "actions": {"Fee": 0},
                    },
                ],
            },
        }

        with temp_config(transforms_config):
            df = pd.DataFrame(
                {
                    Column.Txn.ACTION: ["BUY", "DIVIDEND", "SELL"],
                    Column.Txn.TICKER: ["USD.CAD", "AAPL", "USD.CAD"],
                    "Fee": [FEE_AMOUNT, 5.0, FEE_AMOUNT],
                },
            )

            result = TransactionTransformer.transform(df)

            # First rule should transform BUY/SELL USD.CAD to FXT
            assert result.iloc[0][Column.Txn.ACTION] == "FXT"
            assert pd.isna(result.iloc[0][Column.Txn.TICKER])
            assert result.iloc[2][Column.Txn.ACTION] == "FXT"
            assert pd.isna(result.iloc[2][Column.Txn.TICKER])

            # Second rule should set fee to 0 for dividends
            assert result.iloc[1]["Fee"] == 0

            # Fee for FXT transactions should remain unchanged
            assert result.iloc[0]["Fee"] == FEE_AMOUNT
            assert result.iloc[2]["Fee"] == FEE_AMOUNT

    def test_no_matching_conditions(self, temp_config: TempConfigType) -> None:
        """Test transformation with no matching conditions."""
        transforms_config = {
            "transforms": {
                "rules": [
                    {
                        "conditions": {"Action": ["SELL"], "Ticker": ["EUR.USD"]},
                        "actions": {"Action": "FXT"},
                    },
                ],
            },
        }

        with temp_config(transforms_config):
            df = pd.DataFrame(
                {
                    Column.Txn.ACTION: ["BUY", "DIVIDEND"],
                    Column.Txn.TICKER: ["USD.CAD", "AAPL"],
                },
            )

            result = TransactionTransformer.transform(df)

            # No transformations should be applied
            pd.testing.assert_frame_equal(result, df)

    def test_missing_condition_field(self, temp_config: TempConfigType) -> None:
        """Test transformation when condition field is missing from data."""
        transforms_config = {
            "transforms": {
                "rules": [
                    {
                        "conditions": {"NonExistentField": ["VALUE"]},
                        "actions": {"Action": "FXT"},
                    },
                ],
            },
        }

        with temp_config(transforms_config):
            df = pd.DataFrame(
                {
                    Column.Txn.ACTION: ["BUY"],
                    Column.Txn.TICKER: ["AAPL"],
                },
            )

            result = TransactionTransformer.transform(df)

            # No transformations should be applied
            pd.testing.assert_frame_equal(result, df)

    def test_missing_action_field(self, temp_config: TempConfigType) -> None:
        """Test transformation when action field is missing from data."""
        transforms_config = {
            "transforms": {
                "rules": [
                    {
                        "conditions": {"Action": ["BUY"]},
                        "actions": {"NonExistentField": "VALUE"},
                    },
                ],
            },
        }

        with temp_config(transforms_config):
            df = pd.DataFrame(
                {
                    Column.Txn.ACTION: ["BUY"],
                    Column.Txn.TICKER: ["AAPL"],
                },
            )

            result = TransactionTransformer.transform(df)

            # Original data should remain unchanged (action field doesn't exist)
            pd.testing.assert_frame_equal(result, df)
