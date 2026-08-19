"""Tests for the Amount/Units sign-correction step of transaction formatting."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pytest

from ingest.validation import ActionValidationRules, TransactionFormatter
from utils.constants import Action, Column, Currency, Sign

if TYPE_CHECKING:
    from tests.test_types import TempContext

# Inside the mock data range, so settlement calculation uses preloaded calendars.
TXN_DATE = "2025-08-15"
AMOUNT_MAGNITUDE = 1502.50
UNITS_MAGNITUDE = 10.0

# Derived straight from the production rule table, so a new constrained action
# (or a changed sign) is picked up here without touching this file.
SIGNED_FIELD_CASES: list[tuple[Action, float | None, float | None]] = [
    (
        action,
        AMOUNT_MAGNITUDE
        if rules.get(Column.Txn.AMOUNT) is Sign.POSITIVE
        else -AMOUNT_MAGNITUDE
        if rules.get(Column.Txn.AMOUNT) is Sign.NEGATIVE
        else None,
        UNITS_MAGNITUDE
        if rules.get(Column.Txn.UNITS) is Sign.POSITIVE
        else -UNITS_MAGNITUDE
        if rules.get(Column.Txn.UNITS) is Sign.NEGATIVE
        else None,
    )
    for action, rules in ActionValidationRules.SIGN_RULES.items()
]
UNCONSTRAINED_ACTIONS: list[Action] = [
    action for action in Action if action not in ActionValidationRules.SIGN_RULES
]


def _format_one(action: str, amount: str, units: str) -> pd.Series:
    """Run a single transaction through the formatter and return the result."""
    df = pd.DataFrame(
        [
            {
                Column.Txn.TXN_DATE: TXN_DATE,
                Column.Txn.ACTION: action,
                Column.Txn.AMOUNT: amount,
                Column.Txn.CURRENCY: Currency.USD.value,
                Column.Txn.PRICE: "150.25",
                Column.Txn.UNITS: units,
                Column.Txn.TICKER: "AAPL",
                Column.Txn.ACCOUNT: "TESTACCT",
            },
        ],
    )
    formatted_df, excluded_df = TransactionFormatter.format_and_validate(df)
    assert excluded_df.empty, f"{action} unexpectedly rejected"
    return formatted_df.iloc[0]


class TestSignNormalization:
    """Amount and Units signs are corrected to each action's real direction."""

    @pytest.mark.parametrize(
        ("action", "expected_amount", "expected_units"),
        SIGNED_FIELD_CASES,
    )
    def test_wrong_signs_are_corrected(
        self,
        temp_ctx: TempContext,
        action: Action,
        expected_amount: float | None,
        expected_units: float | None,
    ) -> None:
        """A value with the wrong sign is flipped to match the action."""
        with temp_ctx():
            # Deliberately opposite to what each action requires.
            wrong_amount = (
                str(AMOUNT_MAGNITUDE)
                if expected_amount is None
                else str(-expected_amount)
            )
            wrong_units = (
                str(UNITS_MAGNITUDE) if expected_units is None else str(-expected_units)
            )
            row = _format_one(action.value, wrong_amount, wrong_units)

            if expected_amount is not None:
                assert float(row[Column.Txn.AMOUNT]) == pytest.approx(expected_amount)
            if expected_units is not None:
                assert float(row[Column.Txn.UNITS]) == pytest.approx(expected_units)

    @pytest.mark.parametrize(
        ("action", "amount", "units"),
        [
            (
                action,
                expected_amount or AMOUNT_MAGNITUDE,
                expected_units or UNITS_MAGNITUDE,
            )
            for action, expected_amount, expected_units in SIGNED_FIELD_CASES
        ],
    )
    def test_correct_signs_are_left_alone(
        self,
        temp_ctx: TempContext,
        action: Action,
        amount: float,
        units: float,
    ) -> None:
        """Values that already satisfy the rule pass through untouched."""
        with temp_ctx():
            row = _format_one(action.value, str(amount), str(units))
            assert float(row[Column.Txn.AMOUNT]) == pytest.approx(amount)
            assert float(row[Column.Txn.UNITS]) == pytest.approx(units)

    @pytest.mark.parametrize("action", UNCONSTRAINED_ACTIONS)
    @pytest.mark.parametrize("amount", ["-45.00", "45.00"])
    def test_unconstrained_actions_keep_their_sign(
        self,
        temp_ctx: TempContext,
        action: Action,
        amount: str,
    ) -> None:
        """Actions with no sign rule are valid either way, so neither is flipped."""
        with temp_ctx():
            row = _format_one(action.value, amount, "10")
            assert float(row[Column.Txn.AMOUNT]) == pytest.approx(float(amount))

    def test_precision_survives_the_flip(self, temp_ctx: TempContext) -> None:
        """Correcting a sign must not round-trip through a float."""
        with temp_ctx():
            row = _format_one(Action.BUY.value, "1234.1234567891", "10")
            assert row[Column.Txn.AMOUNT] == "-1234.1234567891"

    def test_zero_is_left_alone(self, temp_ctx: TempContext) -> None:
        """Zero has no direction, so it is never flipped."""
        with temp_ctx():
            row = _format_one(Action.BUY.value, "0", "10")
            assert float(row[Column.Txn.AMOUNT]) == pytest.approx(0.0)
