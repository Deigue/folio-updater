"""Engine for displaying all information about a ticker.

Nothing is valued here. Pool values come from the same dashboard backend. Fundamentals
and Performance come from cached Quotes, or are requested on-demand.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from domain import Column, Currency, PriceRange, WarningCode
from domain.numeric import ZERO
from engine.panels import FOLIO_VIEW, account_view, type_view

if TYPE_CHECKING:
    from collections.abc import Mapping
    from decimal import Decimal

    from engine.panels import FolioValuation, Panel, PoolView
    from engine.performance import RangeMove
    from engine.positions import Holding
    from services.quotes_service import Quote


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


@dataclass(frozen=True)
class PerformanceRow:
    """One security's line in the performance matrix.

    Attributes:
        symbol: The canonical symbol.
        quote: Its live quote.
        moves: Its move over every range.
        market: The pool's market value of it, in the valuation's currency.
            None for a security the pool does not hold.
        weight_in_pool: Its share of the pool shown.
        weight_in_folio: Its share of the whole portfolio.
    """

    symbol: str
    quote: Quote
    moves: Mapping[PriceRange, RangeMove | None]
    market: Decimal | None = None
    weight_in_pool: Decimal | None = None
    weight_in_folio: Decimal | None = None


# What the matrix can be ordered by: its market value, or its move over a range.
MARKET_SORT = "market"
MATRIX_SORTS: tuple[str, ...] = (MARKET_SORT, *(str(r).lower() for r in PriceRange))
# A range's lower-case name back to the range, for `--sort ytd`.
_RANGE_NAMES = {str(r).lower(): str(r) for r in PriceRange}


class UnknownMatrixSortError(ValueError):
    """A `--sort` naming neither the market value nor a range."""

    def __init__(self, name: str) -> None:
        """Report the bad input, and present the valid options."""
        self.name = name
        super().__init__(
            f"Cannot sort by '{name}'. Try one of: {', '.join(MATRIX_SORTS)}.",
        )


def performance_rows(
    panel: Panel | None,
    quotes: Mapping[str, Quote],
    moves: Mapping[str, Mapping[PriceRange, RangeMove | None]],
) -> list[PerformanceRow]:
    """Line securities up for comparison, with what a pool holds of each.

    Args:
        panel: The pool whose holdings supply the market value and weights, or
            None when there is no folio to value.
        quotes: Each security's quote, in the order to list them.
        moves: Each security's move over every range.

    Returns:
        One row per security, in the order given. A security the pool does not
        hold has no market value or weights.
    """
    held = {} if panel is None else {h.symbol: h for h in panel.holdings.holdings}
    rows: list[PerformanceRow] = []
    for symbol, quote in quotes.items():
        holding = held.get(symbol)
        rows.append(
            PerformanceRow(
                symbol=symbol,
                quote=quote,
                moves=moves.get(symbol, {}),
                market=holding.market_value if holding else None,
                weight_in_pool=holding.weight_in_pool if holding else None,
                weight_in_folio=holding.weight_in_folio if holding else None,
            ),
        )
    return rows


def sort_performance(
    rows: list[PerformanceRow],
    name: str,
    *,
    reverse: bool = False,
) -> list[PerformanceRow]:
    """Order the matrix by market value or by the move over one range.

    Largest first, the way a reader asks "which did best"; `reverse` flips it.
    A row with nothing to compare stays at the bottom either way.

    Args:
        rows: The rows to order.
        name: `market`, or a range such as `1wk`, matched case-insensitively.
        reverse: Smallest first instead.

    Returns:
        A new, ordered list.

    Raises:
        UnknownMatrixSortError: If `name` is neither.
    """
    wanted = name.strip().lower()
    if wanted not in MATRIX_SORTS:
        raise UnknownMatrixSortError(name)

    def measure(row: PerformanceRow) -> Decimal | None:
        if wanted == MARKET_SORT:
            return row.market
        move = row.moves.get(PriceRange(_RANGE_NAMES[wanted]))
        return None if move is None else move.change_pct

    present = [row for row in rows if measure(row) is not None]
    missing = [row for row in rows if measure(row) is None]
    present.sort(key=lambda row: measure(row) or ZERO, reverse=not reverse)
    return [*present, *missing]
