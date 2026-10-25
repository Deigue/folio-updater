"""Tests for the transform rules, merge groups and cancellations."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from domain import Column
from ingest.rules import TransactionTransformer

if TYPE_CHECKING:
    from .helpers.context import TempContext

FEE_AMOUNT = 9.95


def _column(result: pd.DataFrame, column: str) -> list[object]:
    """Read a column as a list, with every missing value as None."""
    return [None if pd.isna(value) else value for value in result[column]]


@pytest.mark.parametrize(
    ("test_data", "rules", "expected"),
    [
        pytest.param(
            {
                Column.Txn.ACTION: ["BUY", "DIVIDEND", "SELL"],
                Column.Txn.TICKER: ["USD.CAD", "AAPL", "USD.CAD"],
                "Fee": [FEE_AMOUNT, 5.0, FEE_AMOUNT],
            },
            [
                {
                    "conditions": {"Action": ["BUY", "SELL"], "Ticker": ["USD.CAD"]},
                    "actions": {"Action": "FXT", "Ticker": ""},
                },
                {"conditions": {"Action": ["DIVIDEND"]}, "actions": {"Fee": 0}},
            ],
            {
                Column.Txn.ACTION: ["FXT", "DIVIDEND", "FXT"],
                Column.Txn.TICKER: [None, "AAPL", None],
                "Fee": [FEE_AMOUNT, 0, FEE_AMOUNT],
            },
            id="multiple_rules",
        ),
        pytest.param(
            {
                Column.Txn.ACTION: ["BUY", "DIVIDEND"],
                Column.Txn.TICKER: ["USD.CAD", "AAPL"],
            },
            [
                {
                    "conditions": {"Action": ["SELL"], "Ticker": ["EUR.USD"]},
                    "actions": {"Action": "FXT"},
                },
            ],
            {Column.Txn.ACTION: ["BUY", "DIVIDEND"]},
            id="no_match",
        ),
        pytest.param(
            {Column.Txn.ACTION: ["BUY"], Column.Txn.TICKER: ["AAPL"]},
            [
                {
                    "conditions": {"NonExistentField": ["VALUE"]},
                    "actions": {"Action": "FXT"},
                },
            ],
            {Column.Txn.ACTION: ["BUY"]},
            id="missing_condition_field",
        ),
        pytest.param(
            {Column.Txn.ACTION: ["BUY"], Column.Txn.TICKER: ["AAPL"]},
            [
                {
                    "conditions": {"Action": ["BUY"]},
                    "actions": {"NonExistentField": "VALUE"},
                },
            ],
            {Column.Txn.ACTION: ["BUY"]},
            id="missing_action_field",
        ),
        # "contains:" condition matches a substring, case-insensitively
        pytest.param(
            {
                Column.Txn.ACTION: ["BRW", "BRW", "BRW"],
                "Description": [
                    "JOURNAL   -   journal position to account 123",
                    "JOURNAL   -   Journal Position From account 123",
                    "JOURNAL   -   unrelated activity",
                ],
            },
            [
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
            {Column.Txn.ACTION: ["TFR_OUT", "TFR_IN", "BRW"]},
            id="contains_prefix",
        ),
    ],
)
def test_transform_scenarios(
    temp_ctx: TempContext,
    test_data: dict,
    rules: list[dict],
    expected: dict[str, list[object]],
) -> None:
    """Each scenario's rules leave every named column as expected."""
    with temp_ctx({"transforms": {"rules": rules}}):
        result = TransactionTransformer.transform(pd.DataFrame(test_data)).df
    for column, values in expected.items():
        assert _column(result, column) == values, column


def test_a_numeric_condition_matches_by_value(temp_ctx: TempContext) -> None:
    """A rule keyed on `Units` finds 10.0 from "10", and sets an integer column."""
    config = {
        "transforms": {
            "rules": [
                {"conditions": {"Units": ["10"]}, "actions": {"Lots": "3"}},
            ],
        },
    }
    with temp_ctx(config):
        df = pd.DataFrame({Column.Txn.UNITS: [10.0, 5.0], "Lots": [1, 1]})
        result = TransactionTransformer.transform(df).df

    assert result["Lots"].tolist() == [3, 1]


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
                ("2026-09-08", "-8000", "ACCT", "CANCELLATION"),
                ("2026-09-07", "8000", "ACCT", "DEPOSIT"),
                ("2026-09-08", "8000", "ACCT", "DEPOSIT"),
            ],
            ["2026-09-08"],
            "2026-09-07",
            id="earliest-on-or-before",
        ),
        pytest.param(
            [
                ("2026-09-08", "-8000", "ACCT", "CANCELLATION"),
                ("2026-09-09", "8000", "ACCT", "DEPOSIT"),
                ("2026-09-07", "8000", "OTHER", "DEPOSIT"),
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
        outcome = TransactionTransformer.transform(_cash_rows(rows))
        result, cancels = outcome.df, outcome.cancels

    assert result[Column.Txn.TXN_DATE].tolist() == kept
    assert len(cancels) == 1
    cancelled = cancels[0].cancelled
    assert (None if cancelled is None else cancelled[Column.Txn.TXN_DATE]) == voided


def test_cancellation_rule_needs_its_fields(temp_ctx: TempContext) -> None:
    """A rule naming a column the data lacks is skipped, leaving every row."""
    rows = _cash_rows([("2026-09-08", "-8000", "ACCT", "CANCELLATION")])
    with temp_ctx(CANCEL_CONFIG):
        outcome = TransactionTransformer.transform(
            rows.drop(columns=[Column.Txn.CURRENCY]),
        )
        result, cancels = outcome.df, outcome.cancels

    assert len(result) == 1
    assert cancels == []


def test_no_cancellation_leaves_rows(temp_ctx: TempContext) -> None:
    """A file with no cancelling row passes through the rule untouched."""
    rows = _cash_rows([("2026-09-07", "8000", "ACCT", "DEPOSIT")])
    with temp_ctx(CANCEL_CONFIG):
        outcome = TransactionTransformer.transform(rows)
        result, cancels = outcome.df, outcome.cancels

    assert len(result) == 1
    assert cancels == []


# --- merge groups --------------------------------------------------------------


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

    def test_groups_with_transforms(self, temp_ctx: TempContext) -> None:
        """Test merging multiple dividend/tax pairs for different tickers."""
        with temp_ctx(MERGE_CONFIG):
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
                    Column.Txn.AMOUNT: [31.25, -4.75, 50.0, -7.5],
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

            result = TransactionTransformer.transform(df).df
            expected_rows = 2
            assert len(result) == expected_rows

            # Check AAPL
            aapl_row = result[result[Column.Txn.TICKER] == "AAPL"].iloc[0]
            assert aapl_row[Column.Txn.ACTION] == "DIVIDEND"
            expected_aapl = Decimal("26.5")
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

    def test_incomplete_pair_not_merged(self, temp_ctx: TempContext) -> None:
        """Test that rows without matching pairs are not merged."""
        with temp_ctx(MERGE_CONFIG):
            # Only one dividend, no withholding tax
            df = pd.DataFrame(
                {
                    Column.Txn.TXN_DATE: ["2025-09-10"],
                    Column.Txn.ACTION: ["Dividends"],
                    Column.Txn.AMOUNT: [31.25],
                    Column.Txn.CURRENCY: ["USD"],
                    Column.Txn.PRICE: [0.0],
                    Column.Txn.UNITS: [1.0],
                    Column.Txn.TICKER: ["AAPL"],
                    Column.Txn.ACCOUNT: ["MOCK"],
                },
            )

            result = TransactionTransformer.transform(df).df
            assert len(result) == 1
            assert result.iloc[0][Column.Txn.ACTION] == "Dividends"

    def test_different_dates_not_merged(self, temp_ctx: TempContext) -> None:
        """Test that transactions on different dates are not merged."""
        with temp_ctx(MERGE_CONFIG):
            df = pd.DataFrame(
                {
                    Column.Txn.TXN_DATE: ["2025-09-10", "2025-09-11"],
                    Column.Txn.ACTION: ["Dividends", "Withholding Tax"],
                    Column.Txn.AMOUNT: [31.25, -4.75],
                    Column.Txn.CURRENCY: ["USD", "USD"],
                    Column.Txn.PRICE: [0.0, 0.0],
                    Column.Txn.UNITS: [1.0, 1.0],
                    Column.Txn.TICKER: ["AAPL", "AAPL"],
                    Column.Txn.ACCOUNT: ["MOCK", "MOCK"],
                },
            )

            result = TransactionTransformer.transform(df).df
            assert len(result) == len(df)

    @pytest.mark.parametrize(
        "changes",
        [
            pytest.param({"match_fields": ["TxnDate", "Nope"]}, id="match-field"),
            pytest.param({"amount_field": "Nope"}, id="amount-field"),
            pytest.param({"source_actions": ["Interest", "Fee"]}, id="no-rows"),
            pytest.param(
                {"source_actions": ["Dividends", "Withholding Tax", "Fee"]},
                id="missing-action",
            ),
        ],
    )
    def test_a_rule_that_cannot_apply_leaves_the_rows(
        self,
        temp_ctx: TempContext,
        changes: dict[str, object],
    ) -> None:
        """A merge rule naming a missing column or with nothing to merge is skipped."""
        group = {**MERGE_CONFIG["transforms"]["merge_groups"][0], **changes}
        with temp_ctx({"transforms": {"merge_groups": [group]}}):
            df = pd.DataFrame(
                {
                    Column.Txn.TXN_DATE: ["2025-09-10", "2025-09-10"],
                    Column.Txn.ACTION: ["Dividends", "Withholding Tax"],
                    Column.Txn.AMOUNT: [31.25, -4.75],
                    Column.Txn.TICKER: ["AAPL", "AAPL"],
                    Column.Txn.ACCOUNT: ["MOCK", "MOCK"],
                },
            )

            outcome = TransactionTransformer.transform(df)

        assert outcome.df[Column.Txn.AMOUNT].tolist() == [31.25, -4.75]
        assert outcome.merges == []
