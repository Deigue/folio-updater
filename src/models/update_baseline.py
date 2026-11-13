"""One pool's totals as the `UpdateBaselines` table stores them."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Self

from domain import Column, Currency, Grain
from models.cells import (
    read_number,
    required_moment,
    required_number,
    required_text,
    stored_moment,
    stored_number,
)
from models.columns import Kind, stored

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    from decimal import Decimal

_COL = Column.UpdateBaseline


@dataclass(frozen=True, kw_only=True)
class UpdateBaseline:
    """One pool's totals when the latest update or import finished.

    Flows stay in the currency they moved in, so a later rate never revalues
    the dividends and deposits already counted.

    Attributes:
        grain: The grain the pool sits at.
        pool: The account or account type, empty for the folio pool.
        saved_at: When the update or import saved it.
        market_value: Holdings plus cash in CAD, or None when nothing held
            could be priced.
        net_deposits_cad: Net deposits made in CAD.
        net_deposits_usd: Net deposits made in USD.
        dividends_cad: Dividends paid in CAD.
        dividends_usd: Dividends paid in USD.
        txn_count: How many transactions the pool had.
    """

    grain: Grain = field(metadata=stored(_COL.GRAIN, key=True, required=True))
    pool: str = field(metadata=stored(_COL.POOL, key=True, required=True))
    saved_at: datetime = field(metadata=stored(_COL.SAVED_AT, required=True))
    market_value: Decimal | None = field(
        metadata=stored(_COL.MARKET_VALUE, Kind.NUMBER),
    )
    net_deposits_cad: Decimal = field(
        metadata=stored(_COL.NET_DEPOSITS_CAD, Kind.NUMBER, required=True),
    )
    net_deposits_usd: Decimal = field(
        metadata=stored(_COL.NET_DEPOSITS_USD, Kind.NUMBER, required=True),
    )
    dividends_cad: Decimal = field(
        metadata=stored(_COL.DIVIDENDS_CAD, Kind.NUMBER, required=True),
    )
    dividends_usd: Decimal = field(
        metadata=stored(_COL.DIVIDENDS_USD, Kind.NUMBER, required=True),
    )
    txn_count: int = field(
        metadata=stored(_COL.TXN_COUNT, Kind.INTEGER, required=True),
    )

    @property
    def net_deposits(self) -> dict[Currency, Decimal]:
        """Net deposits by the currency they were made in."""
        return {
            Currency.CAD: self.net_deposits_cad,
            Currency.USD: self.net_deposits_usd,
        }

    @property
    def dividends(self) -> dict[Currency, Decimal]:
        """Dividends by the currency they were paid in."""
        return {Currency.CAD: self.dividends_cad, Currency.USD: self.dividends_usd}

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> Self:
        """Read a pool's baseline out of its row.

        Args:
            row: The row, keyed by column name.

        Returns:
            The baseline.
        """
        return cls(
            grain=Grain(required_text(row.get(_COL.GRAIN), _COL.GRAIN)),
            pool=required_text(row.get(_COL.POOL), _COL.POOL),
            saved_at=required_moment(row.get(_COL.SAVED_AT), _COL.SAVED_AT),
            market_value=read_number(row.get(_COL.MARKET_VALUE)),
            net_deposits_cad=required_number(
                row.get(_COL.NET_DEPOSITS_CAD),
                _COL.NET_DEPOSITS_CAD,
            ),
            net_deposits_usd=required_number(
                row.get(_COL.NET_DEPOSITS_USD),
                _COL.NET_DEPOSITS_USD,
            ),
            dividends_cad=required_number(
                row.get(_COL.DIVIDENDS_CAD),
                _COL.DIVIDENDS_CAD,
            ),
            dividends_usd=required_number(
                row.get(_COL.DIVIDENDS_USD),
                _COL.DIVIDENDS_USD,
            ),
            txn_count=int(required_number(row.get(_COL.TXN_COUNT), _COL.TXN_COUNT)),
        )

    def to_row(self) -> dict[str, object]:
        """Return the row this baseline is stored as, keyed by column name."""
        return {
            str(_COL.GRAIN): str(self.grain),
            str(_COL.POOL): self.pool,
            str(_COL.SAVED_AT): stored_moment(self.saved_at),
            str(_COL.MARKET_VALUE): stored_number(self.market_value),
            str(_COL.NET_DEPOSITS_CAD): stored_number(self.net_deposits_cad),
            str(_COL.NET_DEPOSITS_USD): stored_number(self.net_deposits_usd),
            str(_COL.DIVIDENDS_CAD): stored_number(self.dividends_cad),
            str(_COL.DIVIDENDS_USD): stored_number(self.dividends_usd),
            str(_COL.TXN_COUNT): self.txn_count,
        }
