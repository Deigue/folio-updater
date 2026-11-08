"""A Bank of Canada FX rate as the `FX` table stores it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from domain import Column
from models.cells import required_number, required_text, stored_number

if TYPE_CHECKING:
    from collections.abc import Mapping
    from decimal import Decimal


@dataclass(frozen=True, kw_only=True)
class FxRate:
    """One day's USD/CAD rate, both ways.

    Attributes:
        date: The day the rate is for, `YYYY-MM-DD`.
        usd_cad: CAD per USD.
        cad_usd: USD per CAD.
    """

    date: str
    usd_cad: Decimal
    cad_usd: Decimal

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> Self:
        """Read a rate out of its row.

        Args:
            row: The row, keyed by column name.

        Returns:
            The rate.
        """
        return cls(
            date=required_text(row.get(Column.FX.DATE), Column.FX.DATE),
            usd_cad=required_number(
                row.get(Column.FX.FXUSDCAD),
                Column.FX.FXUSDCAD,
            ),
            cad_usd=required_number(
                row.get(Column.FX.FXCADUSD),
                Column.FX.FXCADUSD,
            ),
        )

    def to_row(self) -> dict[str, object]:
        """Return the row this rate is stored as, keyed by column name."""
        return {
            str(Column.FX.DATE): self.date,
            str(Column.FX.FXUSDCAD): stored_number(self.usd_cad),
            str(Column.FX.FXCADUSD): stored_number(self.cad_usd),
        }
