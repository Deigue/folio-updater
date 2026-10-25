"""Decimal helpers for money, units and average-cost arithmetic."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal(0)
ONE = Decimal(1)

# One cent, the exponent money is quantised to.
_Q2 = Decimal("0.01")


def dec(value: object) -> Decimal:
    """Coerce a raw database or CLI value to a Decimal.

    Handles every shape a `Txns` cell arrives in: a float from
    `pd.read_sql_query`, a TEXT string from the dynamically added `Fee` column,
    `None` from a nullable column, and pandas `NaN` / `NA`

    Args:
        value: Raw cell value of any type.

    Returns:
        The value as a Decimal, or ZERO when it is missing or unparseable.
    """
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value if value.is_finite() else ZERO
    if isinstance(value, float):
        # NaN and the infinities are all "no value" as far as a folio goes.
        return ZERO if not math.isfinite(value) else Decimal(str(value))
    if isinstance(value, int):
        return Decimal(value)
    return _from_text(str(value).strip())


# Lowercased text a nullable cell arrives as when it holds nothing.
MISSING_TEXT = frozenset({"nan", "none", "<na>", "nat"})


def _from_text(text: str) -> Decimal:
    """Parse a stored TEXT number, treating anything unusable as ZERO."""
    if not text or text.lower() in MISSING_TEXT:
        return ZERO
    try:
        parsed = Decimal(text)
    except ArithmeticError:
        return ZERO
    return parsed if parsed.is_finite() else ZERO


def q2(value: Decimal) -> Decimal:
    """Round money to the cent, a half cent going up as brokers and CRA forms do."""
    return value.quantize(_Q2, rounding=ROUND_HALF_UP)


def safe_div(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Divide, returning ZERO instead of raising when the denominator is zero.

    Args:
        numerator: Value to divide.
        denominator: Value to divide by.

    Returns:
        The quotient, or ZERO when `denominator` is zero.
    """
    if denominator == ZERO:
        return ZERO
    return numerator / denominator
