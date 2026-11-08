"""A ticker rename as the `TickerAliases` table stores it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Self

from domain import Column
from models.cells import required_text
from models.columns import Kind, stored

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, kw_only=True)
class TickerAlias:
    """One ticker renamed to another from a given day.

    Attributes:
        old_ticker: The ticker before the rename. A ticker is renamed only once.
        new_ticker: The ticker after it.
        effective_date: The day the rename took effect, `YYYY-MM-DD`.
    """

    old_ticker: str = field(metadata=stored(Column.Aliases.OLD_TICKER, key=True))
    new_ticker: str = field(
        metadata=stored(Column.Aliases.NEW_TICKER, required=True),
    )
    effective_date: str = field(
        metadata=stored(Column.Aliases.EFFECTIVE_DATE, Kind.DATE, required=True),
    )

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> Self:
        """Read a rename out of its row.

        Args:
            row: The row, keyed by column name.

        Returns:
            The rename.
        """
        return cls(
            old_ticker=required_text(
                row.get(Column.Aliases.OLD_TICKER),
                Column.Aliases.OLD_TICKER,
            ),
            new_ticker=required_text(
                row.get(Column.Aliases.NEW_TICKER),
                Column.Aliases.NEW_TICKER,
            ),
            effective_date=required_text(
                row.get(Column.Aliases.EFFECTIVE_DATE),
                Column.Aliases.EFFECTIVE_DATE,
            ),
        )

    def to_row(self) -> dict[str, object]:
        """Return the row this rename is stored as, keyed by column name."""
        return {
            str(Column.Aliases.OLD_TICKER): self.old_ticker,
            str(Column.Aliases.NEW_TICKER): self.new_ticker,
            str(Column.Aliases.EFFECTIVE_DATE): self.effective_date,
        }
