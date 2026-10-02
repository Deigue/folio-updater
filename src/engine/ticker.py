"""Engine for displaying all information about a ticker.

Nothing is valued here. Pool values come from the same dashboard backend. Fundamentals
and Performance come from cached Quotes, or are requested on-demand.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from domain import Column, Currency, WarningCode
from domain.numeric import ZERO
from engine.panels import FOLIO_VIEW, account_view, type_view

if TYPE_CHECKING:
    from decimal import Decimal

    from engine.panels import FolioValuation, Panel, PoolView
    from engine.positions import Holding


@dataclass(frozen=True)
class PoolLine:
    """One pool's holding of the security.

    Attributes:
        view: The account type or account.
        holding: Its position there, open or closed.
    """

    view: PoolView
    holding: Holding


@dataclass(frozen=True)
class SymbolPosition:
    """A security representation across the folio.

    Attributes:
        symbol: The canonical symbol.
        lines: Each pool that has ever held it: open positions first, largest
            first, then the closed ones.
        total: The portfolio-wide holding. Read from the portfolio grain
        first_traded: The folio's first transaction in it, `YYYY-MM-DD`.
        currency: The currency every figure is expressed in.
        fx_rate: The USDCAD rate a converted market value was read at, when
            the figures needed one.
        fx_date: The date that rate is from.
    """

    symbol: str
    lines: tuple[PoolLine, ...]
    total: Holding
    first_traded: str
    currency: Currency
    fx_rate: Decimal | None = None
    fx_date: str | None = None

    def held_for(self, today: date) -> tuple[int, int]:
        """Measure how long since the first transaction, in years and months.

        Args:
            today: The day to measure to.

        Returns:
            `(years, months)`, whole months only.
        """
        first = date.fromisoformat(self.first_traded)
        months = (today.year - first.year) * 12 + today.month - first.month
        if today.day < first.day:
            months -= 1
        return divmod(max(months, 0), 12)

    @property
    def flags(self) -> tuple[WarningCode, ...]:
        """Every diagnostic raised against it in any pool, each once."""
        seen: dict[WarningCode, None] = {}
        for holding in (self.total, *(line.holding for line in self.lines)):
            seen.update(dict.fromkeys(holding.flags))
        return tuple(seen)


def symbol_position(
    valuation: FolioValuation,
    symbol: str,
    *,
    by_account: bool = False,
) -> SymbolPosition | None:
    """Find a security in every pool of a valued folio.

    Args:
        valuation: The priced folio.
        symbol: The canonical symbol.
        by_account: One line per broker account rather than per account type.

    Returns:
        The security's position, or None when the folio never traded it or the
        valuation's currency hides it.
    """
    frame = valuation.frame
    rows = frame[frame["Symbol"] == symbol]
    if rows.empty:
        return None
    folio = valuation.panel(FOLIO_VIEW)
    total = _holding_of(folio, symbol)
    if total is None:
        return None

    if by_account:
        names = rows[str(Column.Txn.ACCOUNT)]
        views = [account_view(name) for name in sorted({str(n) for n in names})]
    else:
        names = rows["AcctType"]
        views = [type_view(name) for name in sorted({str(n) for n in names})]

    lines = [
        PoolLine(panel.view, holding)
        for panel in valuation.panels(views)
        if (holding := _holding_of(panel, symbol)) is not None
    ]
    lines.sort(
        key=lambda line: (
            line.holding.closed,
            -(line.holding.market_value_base or line.holding.book_value_base or ZERO),
        ),
    )

    return SymbolPosition(
        symbol=symbol,
        lines=tuple(lines),
        total=total,
        # Earliest of anything, so a position transferred in from an
        # account the folio does not track still has a start.
        first_traded=str(rows[str(Column.Txn.TXN_DATE)].min()),
        currency=total.currency,
        fx_rate=folio.holdings.fx_rate,
        fx_date=folio.holdings.fx_date,
    )


def _holding_of(panel: Panel, symbol: str) -> Holding | None:
    """Find one symbol's holding in a pool, open or closed."""
    holdings = panel.holdings
    for holding in (*holdings.holdings, *holdings.closed):
        if holding.symbol == symbol:
            return holding
    return None
