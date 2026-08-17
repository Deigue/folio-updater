"""Tests for the engine's FX rate lookup."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from engine.fx_rates import FxRates, FxRateUnavailableError, FxWarning, load_fx_rates
from utils.constants import Currency

from .helpers.seed import seed_fx

if TYPE_CHECKING:
    from .test_types import TempContext

D = Decimal

# A Monday-to-Friday week, then the following Monday. 2024-07-01 is Canada Day:
# the Bank of Canada publishes nothing, but US markets are open.
WEEK = FxRates(
    ("2024-06-26", "2024-06-27", "2024-06-28", "2024-07-02"),
    (D("1.3700"), D("1.3710"), D("1.3720"), D("1.3730")),
)


def test_exact_hit() -> None:
    assert WEEK.as_of("2024-06-27") == ("2024-06-27", D("1.3710"))


def test_weekend_carries_back_to_friday() -> None:
    conversion = WEEK.to_cad(D("100"), "2024-06-29", Currency.USD)
    assert conversion.rate_date == "2024-06-28"
    assert conversion.value == D("137.20")
    assert conversion.warning is FxWarning.CARRIED_BACK


def test_canadian_holiday_carries_back() -> None:
    """Canada Day: US markets settle, the Bank of Canada does not publish."""
    conversion = WEEK.to_cad(D("100"), "2024-07-01", Currency.USD)
    assert conversion.rate_date == "2024-06-28"
    assert conversion.warning is FxWarning.CARRIED_BACK


def test_before_coverage_uses_the_earliest_rate() -> None:
    conversion = WEEK.to_cad(D("100"), "2020-01-01", Currency.USD)
    assert conversion.rate_date == "2024-06-26"
    assert conversion.warning is FxWarning.UNCOVERED


def test_after_coverage_carries_the_latest_forward() -> None:
    conversion = WEEK.to_cad(D("100"), "2030-01-01", Currency.USD)
    assert conversion.rate_date == "2024-07-02"
    assert conversion.warning is FxWarning.STALE


def test_same_currency_needs_no_lookup() -> None:
    conversion = WEEK.to_cad(D("100"), "1900-01-01", Currency.CAD)
    assert conversion.value == D("100")
    assert conversion.warning is None


def test_from_cad_divides_at_full_precision() -> None:
    """CAD to USD divides by FXUSDCAD; the stored reciprocal is never read."""
    conversion = WEEK.from_cad(D("137"), "2024-06-26", Currency.USD)
    assert conversion.value == D("137") / D("1.3700")
    assert conversion.rate == D("1.3700")


def test_round_trip_is_lossless_at_decimal_precision() -> None:
    there = WEEK.to_cad(D("1000"), "2024-06-28", Currency.USD).value
    back = WEEK.from_cad(there, "2024-06-28", Currency.USD).value
    assert back == D("1000")


def test_empty_table_raises_only_when_a_conversion_is_needed() -> None:
    empty = FxRates((), ())
    assert empty.coverage == ("", "")
    assert empty.as_of("2024-06-26") is None
    # A CAD amount needs no rate at all.
    assert empty.to_cad(D("100"), "2024-06-26", Currency.CAD).value == D("100")
    with pytest.raises(FxRateUnavailableError, match="folio getfx"):
        empty.to_cad(D("100"), "2024-06-26", Currency.USD)


def test_unsupported_currency_raises() -> None:
    with pytest.raises(FxRateUnavailableError, match="Only USD/CAD"):
        WEEK.to_cad(D("100"), "2024-06-26", Currency.EUR)


def test_coverage_reports_the_span() -> None:
    assert WEEK.coverage == ("2024-06-26", "2024-07-02")


def test_load_fx_rates_reads_only_fxusdcad(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_fx({"2024-06-26": "1.37", "2024-06-27": "1.372"})
        rates = load_fx_rates()
    assert ctx is not None
    assert rates.dates == ("2024-06-26", "2024-06-27")
    assert rates.usdcad == (D("1.37"), D("1.372"))


def test_load_fx_rates_is_empty_without_a_table(temp_ctx: TempContext) -> None:
    with temp_ctx():
        rates = load_fx_rates()
    assert rates.dates == ()


def test_from_cad_to_cad_is_the_identity() -> None:
    conversion = WEEK.from_cad(D("100"), "1900-01-01", Currency.CAD)
    assert conversion.value == D("100")
    assert conversion.warning is None
