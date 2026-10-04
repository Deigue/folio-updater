"""Tests for transaction transformation functionality."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from .test_types import TempContext
from domain import Column
from ingest.rules import TransactionTransformer

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
        # "contains:" condition matches a substring, case-insensitively
        (
            "contains_prefix",
            {
                Column.Txn.ACTION: ["BRW", "BRW", "BRW"],
                "Description": [
                    "JOURNAL   -   journal position to account 123",
                    "JOURNAL   -   Journal Position From account 123",
                    "JOURNAL   -   unrelated activity",
                ],
            },
            {
                "transforms": {
                    "rules": [
                        {
                            "conditions": {
                                "Action": ["BRW"],
                                "Description": ["contains:JOURNAL POSITION TO"],
                            },
                            "actions": {"Action": "TFR_OUT"},
                        },
                        {
                            "conditions": {
                                "Action": ["BRW"],
                                "Description": ["contains:JOURNAL POSITION FROM"],
                            },
                            "actions": {"Action": "TFR_IN"},
                        },
                    ],
                },
            },
            lambda r: (
                r.iloc[0][Column.Txn.ACTION] == "TFR_OUT"
                and r.iloc[1][Column.Txn.ACTION] == "TFR_IN"
                and r.iloc[2][Column.Txn.ACTION] == "BRW"
            ),
        ),
    ],
)
def test_transform_scenarios(
    temp_ctx: TempContext,
    scenario: str,  # noqa: ARG001 (used for test naming)
    test_data: dict,
    config: dict,
    validate: Callable,
) -> None:
    """Test various transformation scenarios with parametrized data."""
    with temp_ctx(config):
        df = pd.DataFrame(test_data)
        result, _, _, _ = TransactionTransformer.transform(df)
        assert validate(result)


CANCEL_CONFIG = {
    "transforms": {
        "cancellations": [
            {
                "name": "Broker Cancellation",
                "conditions": {"Description": ["CANCELLATION"]},
                "match_fields": ["Account", "$", "Action"],
            },
        ],
    },
}
DEPOSIT = "Deposits/Withdrawals"


def _cash_rows(rows: list[tuple[str, str, str, str]]) -> pd.DataFrame:
    """Build cash rows from (date, amount, account, description)."""
    return pd.DataFrame(
        {
            Column.Txn.TXN_DATE: [r[0] for r in rows],
            Column.Txn.ACTION: [DEPOSIT] * len(rows),
            Column.Txn.AMOUNT: [r[1] for r in rows],
            Column.Txn.CURRENCY: ["CAD"] * len(rows),
            Column.Txn.ACCOUNT: [r[2] for r in rows],
            "Description": [r[3] for r in rows],
        },
    )


@pytest.mark.parametrize(
    ("rows", "kept", "voided"),
    [
        pytest.param(
            [
                ("2026-09-08", "-13000", "ACCT", "CANCELLATION"),
                ("2026-09-07", "13000", "ACCT", "DEPOSIT"),
                ("2026-09-08", "13000", "ACCT", "DEPOSIT"),
            ],
            ["2026-09-08"],
            "2026-09-07",
            id="earliest-on-or-before",
        ),
        pytest.param(
            [
                ("2026-09-08", "-13000", "ACCT", "CANCELLATION"),
                ("2026-09-09", "13000", "ACCT", "DEPOSIT"),
                ("2026-09-07", "13000", "OTHER", "DEPOSIT"),
                ("2026-09-07", "500", "ACCT", "DEPOSIT"),
            ],
            ["2026-09-09", "2026-09-07", "2026-09-07"],
            None,
            id="nothing-to-void",
        ),
    ],
)
def test_cancellations(
    temp_ctx: TempContext,
    rows: list[tuple[str, str, str, str]],
    kept: list[str],
    voided: str | None,
) -> None:
    """A cancellation drops itself and the earliest matching row before it."""
    with temp_ctx(CANCEL_CONFIG):
        result, _, _, cancels = TransactionTransformer.transform(_cash_rows(rows))

    assert result[Column.Txn.TXN_DATE].tolist() == kept
    assert len(cancels) == 1
    cancelled = cancels[0].cancelled
    assert (None if cancelled is None else cancelled[Column.Txn.TXN_DATE]) == voided


def test_cancellation_rule_needs_its_fields(temp_ctx: TempContext) -> None:
    """A rule naming a column the data lacks is skipped, leaving every row."""
    rows = _cash_rows([("2026-09-08", "-13000", "ACCT", "CANCELLATION")])
    with temp_ctx(CANCEL_CONFIG):
        result, _, _, cancels = TransactionTransformer.transform(
            rows.drop(columns=[Column.Txn.CURRENCY]),
        )

    assert len(result) == 1
    assert cancels == []


def test_no_cancellation_leaves_rows(temp_ctx: TempContext) -> None:
    """A file with no cancelling row passes through the rule untouched."""
    rows = _cash_rows([("2026-09-07", "13000", "ACCT", "DEPOSIT")])
    with temp_ctx(CANCEL_CONFIG):
        result, _, _, cancels = TransactionTransformer.transform(rows)

    assert len(result) == 1
    assert cancels == []
