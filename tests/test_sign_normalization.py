"""Tests for the Amount/Units sign-correction step of transaction formatting."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pytest

from domain import ACTION_PROFILES, Action, Column, Currency, Sign
from ingest.validation import TransactionFormatter

from .helpers.seed import ACCOUNT, TXN_DATE

if TYPE_CHECKING:
    from .helpers.context import TempContext

AMOUNT_MAGNITUDE = 1502.50
UNITS_MAGNITUDE = 10.0

# Derived straight from the action profiles, so a new constrained action
# (or a changed sign) is picked up here without touching this file.
SIGNED_FIELD_CASES: list[tuple[Action, float | None, float | None]] = [
    (
        action,
        AMOUNT_MAGNITUDE
        if profile.signs.get(Column.Txn.AMOUNT) is Sign.POSITIVE
        else -AMOUNT_MAGNITUDE
        if profile.signs.get(Column.Txn.AMOUNT) is Sign.NEGATIVE
        else None,
        UNITS_MAGNITUDE
        if profile.signs.get(Column.Txn.UNITS) is Sign.POSITIVE
        else -UNITS_MAGNITUDE
        if profile.signs.get(Column.Txn.UNITS) is Sign.NEGATIVE
        else None,
    )
    for action, profile in ACTION_PROFILES.items()
    if profile.signs
]
UNCONSTRAINED_ACTIONS: list[Action] = [
    action for action, profile in ACTION_PROFILES.items() if not profile.signs
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
                Column.Txn.ACCOUNT: ACCOUNT,
            },
        ],
    )
    formatted_df, invalid_df = TransactionFormatter.format_and_validate(df)
    assert invalid_df.empty, f"{action} unexpectedly rejected"
    return formatted_df.iloc[0]


def test_every_action_has_a_profile() -> None:
    """No action is left without its impact, required fields and signs."""
    assert set(ACTION_PROFILES) == set(Action)


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
            # Compared as text: a float equality cannot tell "-0" from "0".
            assert row[Column.Txn.AMOUNT] == "0"
