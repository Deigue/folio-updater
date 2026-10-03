"""How far a price has moved over each range, measured to the live price."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from domain import PriceRange
from services.price_history import session_day
from services.quotes_service import QuotesService

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

    from services.price_history import PricePoint, RangeHistory
    from services.quotes_service import Quote
    from services.symbols import SymbolResolver

SESSION_RANGES = frozenset({PriceRange.DAY_1, PriceRange.DAYS_2})


@dataclass(frozen=True)
class RangeMove:
    """A price's move over one range, in the currency it is quoted in.

    Attributes:
        range: The range measured.
        anchor: The official close the move is measured from.
        change: The live price less the anchor, per share.
        change_pct: `change` over the anchor, or None when the anchor is zero.
        points: The range's shape, oldest first, ending at the live price.
        start: Where the range begins: the anchor's date, the first session's
            date for `1d` and `2d`, or for `2h` the UTC timestamp of its first
            bar.
        fill: How much of the range's time axis has elapsed: short of 1 only
            for a session still trading.
    """

    range: PriceRange
    anchor: Decimal
    change: Decimal
    change_pct: Decimal | None
    points: tuple[Decimal, ...]
    start: str
    fill: Decimal = Decimal(1)

    @property
    def multiple(self) -> Decimal | None:
        """The live price as a multiple of the anchor: 2 is a doubling."""
        return None if self.change_pct is None else 1 + self.change_pct


def price_moves(
    quote: Quote,
    history: Mapping[PriceRange, RangeHistory],
    intraday: Mapping[PriceRange, RangeHistory],
) -> dict[PriceRange, RangeMove | None]:
    """Measure the live price against every range's anchor.

    Args:
        quote: The symbol's live quote.
        history: Its stored ranges, `2d` and longer, anchored on official closes.
        intraday: Its live ranges, from bars. Empty when offline, which leaves
            `2h` blank and draws the rest from the stored closes.

    Returns:
        Every range, shortest first, mapped to its move. None where there is
        no live price, or nothing that reaches back that far.
    """
    price = quote.price if quote.priced else None
    moves: dict[PriceRange, RangeMove | None] = {}
    for price_range in PriceRange:
        stored = history.get(price_range)
        live = intraday.get(price_range)
        anchor = _anchor(price_range, quote, stored, live)
        shaped = live or stored
        if price is None or anchor is None:
            moves[price_range] = None
            continue
        change = price - anchor
        if shaped is None:
            # Only the day gets here: its anchor is the quote's previous close,
            # which needs no history. Offline, the move still stands, as a line.
            moves[price_range] = RangeMove(
                range=price_range,
                anchor=anchor,
                change=change,
                change_pct=change / anchor if anchor else None,
                points=(anchor, price),
                start="",
            )
            continue
        session = price_range in SESSION_RANGES and live is not None
        drawn = tuple(point.close for point in shaped.points)
        moves[price_range] = RangeMove(
            range=price_range,
            anchor=anchor,
            change=change,
            change_pct=change / anchor if anchor else None,
            # A session chart starts at the open, with the close before it only
            # as the baseline; any other range starts at its anchor.
            points=(*drawn, price) if session else (anchor, *drawn, price),
            start=(
                session_day(shaped.points[0]).isoformat()
                if session
                else (stored or shaped).anchor.when
            ),
            fill=shaped.fill if session else Decimal(1),
        )
    return moves


def _anchor(
    price_range: PriceRange,
    quote: Quote,
    stored: RangeHistory | None,
    live: RangeHistory | None,
) -> Decimal | None:
    """Find the official close a range is measured from."""
    if price_range is PriceRange.DAY_1 and quote.prev_close is not None:
        # The same previous close `folio dash` measures the day from.
        return quote.prev_close
    if price_range is PriceRange.DAYS_2 and live is not None and stored is not None:
        return _close_before(stored, session_day(live.points[0]))
    if stored is not None:
        return stored.anchor.close
    # Only `2h` has no daily close to stand on: its own first bar is the start.
    return live.anchor.close if live is not None else None


def _close_before(stored: RangeHistory, first: date) -> Decimal:
    """Find the last stored daily close before a window's first session.

    The stored `2d` keeps the last three daily closes, so whichever two sessions
    the bars cover, the close before them is among them.
    """
    closes: list[PricePoint] = [stored.anchor, *stored.points]
    earlier = [point for point in closes if session_day(point) < first]
    return earlier[-1].close if earlier else stored.anchor.close


def fetch_moves(
    quotes: Mapping[str, Quote],
    resolver: SymbolResolver,
    *,
    refresh: bool = False,
    offline: bool = False,
) -> dict[str, dict[PriceRange, RangeMove | None]]:
    """Measure every range for a batch of securities, fetching what is due.

    Stored history is refetched at most once a day, and the intraday bars
    behind `2h` to `1wk` live, both in one batch for every security.

    Args:
        quotes: Each security's live quote, keyed by canonical symbol.
        resolver: Supplies the provider spelling for each symbol.
        refresh: Refetch the stored history regardless of when it was fetched.
        offline: Never touch the network: stored history only, no intraday.

    Returns:
        Each security mapped to its move over every range.
    """
    symbols = list(quotes)
    history = QuotesService.history(symbols, resolver, refresh=refresh, offline=offline)
    intraday = QuotesService.intraday(symbols, resolver, offline=offline)
    return {
        symbol: price_moves(
            quotes[symbol],
            history.get(symbol, {}),
            intraday.get(symbol, {}),
        )
        for symbol in symbols
    }
