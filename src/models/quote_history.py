"""One point of a symbol's condensed price history, as `QuoteHistory` stores it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from domain import Column, PriceRange
from models.cells import (
    required_moment,
    required_number,
    required_text,
    stored_moment,
    stored_number,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    from decimal import Decimal


@dataclass(frozen=True, kw_only=True)
class QuoteHistory:
    """One close on one of a symbol's charts.

    Attributes:
        symbol: The folio's own canonical symbol.
        range: The chart the point belongs to.
        seq: Its place on that chart: 0 is the anchor the move is measured from,
            then the points in date order.
        date: The day of the close, `YYYY-MM-DD`, or a timestamp for an
            intraday bar.
        close: The close, in the symbol's quote currency.
        fetched_at: When this symbol's history was fetched.
    """

    symbol: str
    range: PriceRange
    seq: int
    date: str
    close: Decimal
    fetched_at: datetime

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> Self:
        """Read a point out of its row.

        Args:
            row: The row, keyed by column name.

        Returns:
            The point.
        """
        return cls(
            symbol=required_text(
                row.get(Column.QuoteHistory.SYMBOL),
                Column.QuoteHistory.SYMBOL,
            ),
            range=PriceRange(
                required_text(
                    row.get(Column.QuoteHistory.RANGE),
                    Column.QuoteHistory.RANGE,
                ),
            ),
            seq=int(
                required_number(
                    row.get(Column.QuoteHistory.SEQ),
                    Column.QuoteHistory.SEQ,
                ),
            ),
            date=required_text(
                row.get(Column.QuoteHistory.DATE),
                Column.QuoteHistory.DATE,
            ),
            close=required_number(
                row.get(Column.QuoteHistory.CLOSE),
                Column.QuoteHistory.CLOSE,
            ),
            fetched_at=required_moment(
                row.get(Column.QuoteHistory.FETCHED_AT),
                Column.QuoteHistory.FETCHED_AT,
            ),
        )

    def to_row(self) -> dict[str, object]:
        """Return the row this point is stored as, keyed by column name."""
        return {
            str(Column.QuoteHistory.SYMBOL): self.symbol,
            str(Column.QuoteHistory.RANGE): str(self.range),
            str(Column.QuoteHistory.SEQ): self.seq,
            str(Column.QuoteHistory.DATE): self.date,
            str(Column.QuoteHistory.CLOSE): stored_number(self.close),
            str(Column.QuoteHistory.FETCHED_AT): stored_moment(self.fetched_at),
        }
