"""Tests for transaction transformation functionality."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pytest

if TYPE_CHECKING:
    from contextlib import _GeneratorContextManager
    from typing import Callable

    from app.app_context import AppContext

    TempConfigType = Callable[..., _GeneratorContextManager[AppContext, None, None]]
from db.transformers import TransactionTransformer
from utils.constants import Column

# Test constants
FEE_AMOUNT = 9.95


@pytest.mark.parametrize(
    ("scenario", "test_data", "config", "validate"),
    [
        # Multiple transformations
        (
            "multiple_rules",
            {
                Column.Txn.ACTION: ["BUY", "DIVIDEND", "SELL"],
                Column.Txn.TICKER: ["USD.CAD", "AAPL", "USD.CAD"],
                "Fee": [FEE_AMOUNT, 5.0, FEE_AMOUNT],
            },
            {
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
            },
            lambda r: (
                r.iloc[0][Column.Txn.ACTION] == "FXT"
                and pd.isna(r.iloc[0][Column.Txn.TICKER])
                and r.iloc[0]["Fee"] == FEE_AMOUNT
                and r.iloc[1]["Fee"] == 0
                and r.iloc[2][Column.Txn.ACTION] == "FXT"
                and r.iloc[2]["Fee"] == FEE_AMOUNT
            ),
        ),
        # No matching conditions
        (
            "no_match",
            {
                Column.Txn.ACTION: ["BUY", "DIVIDEND"],
                Column.Txn.TICKER: ["USD.CAD", "AAPL"],
            },
            {
                "transforms": {
                    "rules": [
                        {
                            "conditions": {
                                "Action": ["SELL"],
                                "Ticker": ["EUR.USD"],
                            },
                            "actions": {"Action": "FXT"},
                        },
                    ],
                },
            },
            lambda r: len(r) == 2,
        ),
        # Missing condition field
        (
            "missing_condition_field",
            {
                Column.Txn.ACTION: ["BUY"],
                Column.Txn.TICKER: ["AAPL"],
            },
            {
                "transforms": {
                    "rules": [
                        {
                            "conditions": {"NonExistentField": ["VALUE"]},
                            "actions": {"Action": "FXT"},
                        },
                    ],
                },
            },
            lambda r: r.iloc[0][Column.Txn.ACTION] == "BUY",
        ),
        # Missing action field
        (
            "missing_action_field",
            {
                Column.Txn.ACTION: ["BUY"],
                Column.Txn.TICKER: ["AAPL"],
            },
            {
                "transforms": {
                    "rules": [
                        {
                            "conditions": {"Action": ["BUY"]},
                            "actions": {"NonExistentField": "VALUE"},
                        },
                    ],
                },
            },
            lambda r: r.iloc[0][Column.Txn.ACTION] == "BUY",
        ),
    ],
)
def test_transform_scenarios(
    temp_config: TempConfigType,
    scenario: str,  # noqa: ARG001 (used for test naming)
    test_data: dict,
    config: dict,
    validate: Callable,
) -> None:
    """Test various transformation scenarios with parametrized data."""
    with temp_config(config):
        df = pd.DataFrame(test_data)
        result = TransactionTransformer.transform(df)
        assert validate(result)
