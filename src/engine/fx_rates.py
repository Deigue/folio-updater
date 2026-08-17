"""FX rate lookup for the cost-base engine.

Loaded once per replay and passed as an argument.

Only `FXUSDCAD` is ever read. The stored `FXCADUSD` column is a calculated reciprocal.
Converting CAD to USD divides by `FXUSDCAD` at full Decimal precision instead.
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from db.queries import get_connection, get_rows
from utils.constants import Column, Currency, Table
from utils.numeric import ZERO, dec, safe_div

if TYPE_CHECKING:
    from decimal import Decimal

logger = logging.getLogger(__name__)


class FxRateUnavailableError(RuntimeError):
    """Raised when a conversion is needed and no rate can supply it."""


class FxWarning(StrEnum):
    """Why a conversion used a rate other than an exact same-day one."""

    CARRIED_BACK = "carried_back"  # weekend or holiday; used the prior business day
    STALE = "stale"  # after coverage; used the latest rate we hold
    UNCOVERED = "uncovered"  # before coverage; used the earliest rate we hold


@dataclass(frozen=True)
class Conversion:
    """One currency conversion and the rate that produced it."""

    value: Decimal
    rate: Decimal
    rate_date: str
    warning: FxWarning | None = None


@dataclass(frozen=True)
class FxRates:
    """USD/CAD rates as parallel sorted arrays, looked up by bisection.

    Attributes:
        dates: Business days in `YYYY-MM-DD` form, ascending.
        usdcad: The `FXUSDCAD` rate for each date, positionally aligned.
    """

    dates: tuple[str, ...]
    usdcad: tuple[Decimal, ...]

    def as_of(self, on: str) -> tuple[str, Decimal] | None:
        """Find the rate in force on a date, carrying the last one back.

        Args:
            on: Date in `YYYY-MM-DD` form.

        Returns:
            The `(rate_date, rate)` in force, or None when the table is empty.
            A weekend or holiday resolves to the preceding business day; a date
            before coverage resolves to the earliest rate held.
        """
        if not self.dates:
            return None
        index = bisect_right(self.dates, on) - 1
        if index < 0:
            return self.dates[0], self.usdcad[0]
        return self.dates[index], self.usdcad[index]

    def to_cad(self, amount: Decimal, on: str, frm: Currency) -> Conversion:
        """Convert an amount into CAD, the tax currency.

        Args:
            amount: Value in `frm`.
            on: Date whose rate should be used, `YYYY-MM-DD`.
            frm: Currency the amount is denominated in.

        Returns:
            The converted value alongside the rate that produced it.
        """
        if frm is Currency.CAD:
            return Conversion(amount, dec(1), on)
        rate_date, rate, warning = self._lookup(on, frm)
        return Conversion(amount * rate, rate, rate_date, warning)

    def from_cad(self, amount: Decimal, on: str, to: Currency) -> Conversion:
        """Convert a CAD amount into another currency.

        Args:
            amount: Value in CAD.
            on: Date whose rate should be used, `YYYY-MM-DD`.
            to: Currency to convert into.

        Returns:
            The converted value alongside the rate that produced it. The rate
            reported is always `FXUSDCAD`; the division happens here at full
            Decimal precision rather than reading the stored reciprocal.
        """
        if to is Currency.CAD:
            return Conversion(amount, dec(1), on)
        rate_date, rate, warning = self._lookup(on, to)
        return Conversion(safe_div(amount, rate), rate, rate_date, warning)

    @property
    def coverage(self) -> tuple[str, str]:
        """First and last dates held, or two empty strings when there are none."""
        if not self.dates:
            return "", ""
        return self.dates[0], self.dates[-1]

    def _lookup(
        self,
        on: str,
        other: Currency,
    ) -> tuple[str, Decimal, FxWarning | None]:
        """Resolve the USDCAD rate for a date, classifying how it was found."""
        if other is not Currency.USD:
            msg = (
                f"No exchange rate source for {other}. Only USD/CAD conversion "
                f"is supported; the FX table holds Bank of Canada FXUSDCAD only."
            )
            raise FxRateUnavailableError(msg)

        found = self.as_of(on)
        if found is None:
            msg = (
                f"FX rates are empty, so {other} cannot be converted for {on}. "
                f"Run `folio getfx` to populate them."
            )
            raise FxRateUnavailableError(msg)

        rate_date, rate = found
        if rate_date == on:
            return rate_date, rate, None
        if on < self.dates[0]:
            return rate_date, rate, FxWarning.UNCOVERED
        if on > self.dates[-1]:
            return rate_date, rate, FxWarning.STALE
        return rate_date, rate, FxWarning.CARRIED_BACK


def load_fx_rates() -> FxRates:
    """Read every Bank of Canada rate held in the folio, ascending by date.

    Returns:
        An `FxRates` over the whole `FX` table, empty when it has no rows.
    """
    with get_connection() as conn:
        frame = get_rows(conn, Table.FX, order_by=f'"{Column.FX.DATE}" ASC')

    if frame.empty or Column.FX.FXUSDCAD not in frame.columns:
        return FxRates((), ())

    pairs = [
        (str(date), dec(rate))
        for date, rate in zip(
            frame[Column.FX.DATE].to_numpy(),
            frame[Column.FX.FXUSDCAD].to_numpy(),
            strict=True,
        )
        if dec(rate) > ZERO
    ]
    logger.debug("Loaded %d FX rates for cost-base conversion", len(pairs))
    return FxRates(
        tuple(date for date, _ in pairs),
        tuple(rate for _, rate in pairs),
    )
