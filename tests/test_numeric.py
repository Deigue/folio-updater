"""Tests for the Decimal boundary helpers."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from domain.numeric import ZERO, dec, q2, safe_div

D = Decimal


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ZERO),
        (D("12.5"), D("12.5")),
        (12.5, D("12.5")),  # float from pd.read_sql_query
        (12, D("12")),
        ("12.50", D("12.50")),  # TEXT, the way Fee is stored
        ("  -3.75  ", D("-3.75")),
        ("", ZERO),
        ("nan", ZERO),
        ("None", ZERO),
        ("<NA>", ZERO),
        ("not a number", ZERO),
        (float("nan"), ZERO),
        (float("inf"), ZERO),
        (D("NaN"), ZERO),
        (pd.NA, ZERO),
    ],
)
def test_dec_handles_every_cell_shape(value: object, expected: Decimal) -> None:
    assert dec(value) == expected


def test_dec_avoids_binary_float_drift() -> None:
    """Going via str() is what keeps 0.1 from becoming 0.1000000000000000055."""
    assert dec(0.1) == D("0.1")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (D("1.005"), D("1.01")),  # a half cent rounds up, not to the even neighbour
        (D("1.015"), D("1.02")),
        (D("0.125"), D("0.13")),
        (D("-0.125"), D("-0.13")),  # away from zero, so a debit rounds like a credit
        (D("1.004"), D("1.00")),
    ],
)
def test_q2_rounds_a_half_cent_up(value: Decimal, expected: Decimal) -> None:
    assert q2(value) == expected


def test_safe_div() -> None:
    assert safe_div(D("10"), D("4")) == D("2.5")
    assert safe_div(D("10"), ZERO) == ZERO
    assert safe_div(ZERO, ZERO) == ZERO
