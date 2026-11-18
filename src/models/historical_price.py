"""Historical prices and the days fetched for them, as their tables store them."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Self

from domain import Column
from models.cells import required_number, required_text, stored_number
from models.columns import Kind, stored

if TYPE_CHECKING:
    from collections.abc import Mapping
    from decimal import Decimal


@dataclass(frozen=True, kw_only=True)
class HistoricalPrice:
    """One security's close on one past trading day, as it traded that day.

    Never adjusted for a later split or for dividends, so a stored price never
    changes.

    Attributes:
        symbol: The symbol the security carries today.
        date: The trading day, `YYYY-MM-DD`.
        price: The close, in the symbol's trading currency.
    """

    symbol: str = field(
        metadata=stored(Column.HistoricalPrice.SYMBOL, key=True, required=True),
    )
    date: str = field(
        metadata=stored(Column.HistoricalPrice.DATE, Kind.DATE, key=True),
    )
    price: Decimal = field(
        metadata=stored(Column.HistoricalPrice.PRICE, Kind.NUMBER, required=True),
    )

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> Self:
        """Read a price out of its row, keyed by column name."""
        return cls(
            symbol=required_text(
                row.get(Column.HistoricalPrice.SYMBOL),
                Column.HistoricalPrice.SYMBOL,
            ),
            date=required_text(
                row.get(Column.HistoricalPrice.DATE),
                Column.HistoricalPrice.DATE,
            ),
            price=required_number(
                row.get(Column.HistoricalPrice.PRICE),
                Column.HistoricalPrice.PRICE,
            ),
        )

    def to_row(self) -> dict[str, object]:
        """Return the row this price is stored as, keyed by column name."""
        return {
            str(Column.HistoricalPrice.SYMBOL): self.symbol,
            str(Column.HistoricalPrice.DATE): self.date,
            str(Column.HistoricalPrice.PRICE): stored_number(self.price),
        }


@dataclass(frozen=True, kw_only=True)
class HistoricalPriceCoverage:
    """The days one symbol's historical prices have been fetched for.

    It records what was asked, not what came back: days the provider had no
    price for are covered too, so they are not asked for again.

    Attributes:
        symbol: The symbol the security carries today.
        fetched_from: The first day fetched, `YYYY-MM-DD`.
        fetched_through: The last day fetched, `YYYY-MM-DD`.
    """

    symbol: str = field(
        metadata=stored(
            Column.HistoricalPriceCoverage.SYMBOL,
            key=True,
            required=True,
        ),
    )
    fetched_from: str = field(
        metadata=stored(
            Column.HistoricalPriceCoverage.FETCHED_FROM,
            Kind.DATE,
            required=True,
        ),
    )
    fetched_through: str = field(
        metadata=stored(
            Column.HistoricalPriceCoverage.FETCHED_THROUGH,
            Kind.DATE,
            required=True,
        ),
    )

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> Self:
        """Read a coverage span out of its row, keyed by column name."""
        return cls(
            symbol=required_text(
                row.get(Column.HistoricalPriceCoverage.SYMBOL),
                Column.HistoricalPriceCoverage.SYMBOL,
            ),
            fetched_from=required_text(
                row.get(Column.HistoricalPriceCoverage.FETCHED_FROM),
                Column.HistoricalPriceCoverage.FETCHED_FROM,
            ),
            fetched_through=required_text(
                row.get(Column.HistoricalPriceCoverage.FETCHED_THROUGH),
                Column.HistoricalPriceCoverage.FETCHED_THROUGH,
            ),
        )

    def to_row(self) -> dict[str, object]:
        """Return the row this span is stored as, keyed by column name."""
        return {
            str(Column.HistoricalPriceCoverage.SYMBOL): self.symbol,
            str(Column.HistoricalPriceCoverage.FETCHED_FROM): self.fetched_from,
            str(Column.HistoricalPriceCoverage.FETCHED_THROUGH): self.fetched_through,
        }


@dataclass(frozen=True)
class HistoricalPriceSummary:
    """What the store holds for one symbol.

    Attributes:
        coverage: The days fetched for it.
        days: How many trading days have a price.
        first: Its earliest price, or None when the provider had none.
        last: Its latest price, or None when the provider had none.
    """

    coverage: HistoricalPriceCoverage
    days: int
    first: HistoricalPrice | None
    last: HistoricalPrice | None
