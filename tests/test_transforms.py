"""Tests for the transform rules, merge groups and cancellations."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from app.logging_setup import AUDIT_LOGGER
from domain import Column
from ingest import prepare_transactions
from ingest.rules import NOTHING_TO_VOID, TransactionTransformer

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
        # A value that is not a number is kept as text, even in a number column.
        pytest.param(
            {Column.Txn.ACTION: ["BUY", "DIVIDEND"], "Fee": [FEE_AMOUNT, 5.0]},
            [{"conditions": {"Action": ["DIVIDEND"]}, "actions": {"Fee": "n/a"}}],
            {"Fee": [FEE_AMOUNT, "n/a"]},
            id="text_into_number_column",
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


def _audit_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Return what the audit trail recorded."""
    return [r.getMessage() for r in caplog.records if r.name == AUDIT_LOGGER]


@pytest.mark.parametrize(
    "deposit",
    [
        pytest.param("8000", id="plain"),
        pytest.param("$8,000.00", id="written-as-text"),
    ],
)
def test_a_cancellation_voids_the_earliest_matching_row_on_or_before_it(
    temp_ctx: TempContext,
    deposit: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Both rows leave the import, and the event names the rule."""
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    rows = _cash_rows(
        [
            ("2026-09-08", "-8000", "ACCT", "CANCELLATION"),
            ("2026-09-07", deposit, "ACCT", "DEPOSIT"),
            ("2026-09-08", deposit, "ACCT", "DEPOSIT"),
        ],
    )
    with temp_ctx(CANCEL_CONFIG):
        outcome = TransactionTransformer.transform(rows)

    assert outcome.df[Column.Txn.TXN_DATE].tolist() == ["2026-09-08"]
    assert outcome.invalid.empty
    (event,) = outcome.cancels
    assert event.rule.name == "Broker Cancellation"
    assert event.cancelled[Column.Txn.TXN_DATE] == "2026-09-07"
    stage, cancellation, voided = _audit_lines(caplog)
    assert stage == "  CANCEL 'Broker Cancellation'"
    assert cancellation.startswith("    x TxnDate=2026-09-08|")
    assert voided.startswith("      x TxnDate=2026-09-07|")


def test_a_cancellation_with_nothing_to_void_is_rejected(
    temp_ctx: TempContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A later row, another account or another amount is never voided."""
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    rows = _cash_rows(
        [
            ("2026-09-08", "-8000", "ACCT", "CANCELLATION"),
            ("2026-09-09", "8000", "ACCT", "DEPOSIT"),
            ("2026-09-07", "8000", "OTHER", "DEPOSIT"),
            ("2026-09-07", "500", "ACCT", "DEPOSIT"),
        ],
    )
    with temp_ctx(CANCEL_CONFIG):
        outcome = TransactionTransformer.transform(rows)

    assert outcome.df[Column.Txn.TXN_DATE].tolist() == [
        "2026-09-09",
        "2026-09-07",
        "2026-09-07",
    ]
    assert outcome.cancels == []
    assert outcome.invalid["Description"].tolist() == ["CANCELLATION"]
    assert outcome.invalid[Column.REJECTION_REASON].tolist() == [
        NOTHING_TO_VOID,
    ]
    stage, rejected = _audit_lines(caplog)
    assert stage == "  CANCEL 'Broker Cancellation'"
    assert rejected.startswith("    - TxnDate=2026-09-08|")
    assert rejected.endswith(f"({NOTHING_TO_VOID})")


@pytest.mark.parametrize(
    "blank_account",
    [pytest.param(False, id="alone"), pytest.param(True, id="beside-an-invalid-row")],
)
def test_an_import_rejects_a_cancellation_with_nothing_to_void(
    temp_ctx: TempContext,
    *,
    blank_account: bool,
) -> None:
    """It joins the invalid rows with its reason, and the tally still adds up."""
    rows = _cash_rows(
        [
            ("2026-09-08", "-640", "ACCT", "CANCELLATION"),
            ("2026-09-07", "250", "ACCT", "DEPOSIT"),
            ("2026-09-07", "75", "ACCT", "DEPOSIT"),
        ],
    ).assign(**{Column.Txn.ACTION: "CONTRIBUTION"})
    # Not mapped from a file, so the trade columns are added as empty.
    for column in (Column.Txn.TICKER, Column.Txn.PRICE, Column.Txn.UNITS):
        rows[column] = None
    rows[Column.Txn.FEE] = None
    if blank_account:
        rows.loc[2, Column.Txn.ACCOUNT] = None

    with temp_ctx(CANCEL_CONFIG):
        results = prepare_transactions(rows, map_headers=False)

    reasons = results.invalid_df[Column.REJECTION_REASON].tolist()
    assert reasons[0] == NOTHING_TO_VOID
    assert len(reasons) == (2 if blank_account else 1)
    assert results.cancel_events == []
    # Nothing is stored yet, so the approved rows are what the tally must reach.
    assert len(results.approved) == results.expected_count()


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

    @pytest.mark.parametrize("as_text", [False, True], ids=["typed", "csv_text"])
    def test_groups_with_transforms(
        self,
        temp_ctx: TempContext,
        caplog: pytest.LogCaptureFixture,
        *,
        as_text: bool,
    ) -> None:
        """Merge dividend/tax pairs, also when every column is text as in a CSV."""
        caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
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

            if as_text:
                df = df.astype(str)
            result = TransactionTransformer.transform(df).df
            expected_rows = 2
            assert len(result) == expected_rows

            # Check AAPL
            aapl_row = result[result[Column.Txn.TICKER] == "AAPL"].iloc[0]
            assert aapl_row[Column.Txn.ACTION] == "DIVIDEND"
            expected_aapl = Decimal("26.5")
            actual_aapl = Decimal(str(aapl_row[Column.Txn.AMOUNT]))
            assert actual_aapl == expected_aapl
            assert Decimal(str(aapl_row[Column.Txn.UNITS])) == 0  # From the rule

            # Check SPY
            spy_row = result[result[Column.Txn.TICKER] == "SPY"].iloc[0]
            assert spy_row[Column.Txn.ACTION] == "DIVIDEND"
            expected_spy = Decimal("42.5")
            actual_spy = Decimal(str(spy_row[Column.Txn.AMOUNT]))
            assert actual_spy == expected_spy
            assert Decimal(str(spy_row[Column.Txn.UNITS])) == 0  # From the rule

        # Each merged row is stored, with the source rows it took under it.
        lines = [r.getMessage() for r in caplog.records if r.name == AUDIT_LOGGER]
        merge = next(i for i, line in enumerate(lines) if line.startswith("  MERGE '"))
        assert lines[merge + 1].startswith("    + TxnDate=2025-09-10|Action=DIVIDEND")
        assert lines[merge + 2].startswith("      - TxnDate=2025-09-10|Action=Div")
        assert lines[merge + 3].startswith("      - TxnDate=2025-09-10|Action=With")

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
