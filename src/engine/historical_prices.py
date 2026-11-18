"""The days each security needs historical prices for, and topping them up."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from domain import Column
from domain.dates import today_in_toronto
from domain.symbols import cad_twin
from models import DateWindow
from services.historical_prices import HistoricalPriceService, HistoricalPriceUpdate

if TYPE_CHECKING:
    from engine.ledger import Ledger

_SYMBOL = str(Column.Ledger.SYMBOL)
_TXN_DATE = str(Column.Txn.TXN_DATE)


def price_spans(ledger: Ledger, *, today: date) -> dict[str, DateWindow]:
    """Work out the days each security needs a price for, keyed by today's symbol.

    A security needs prices from its first trade in any account: through
    yesterday while any account still holds it, or through its last transaction
    once sold out everywhere. A gap between a sale and a later buy is not skipped.
    """
    rows = ledger.frame[[_SYMBOL, _TXN_DATE]].dropna()
    days = rows.groupby(_SYMBOL)[_TXN_DATE].agg(["min", "max"])
    held = set(ledger.held_symbols)
    yesterday = today - timedelta(days=1)
    return {
        str(symbol): DateWindow(
            start=date.fromisoformat(str(first)),
            end=yesterday if symbol in held else date.fromisoformat(str(last)),
        )
        for symbol, first, last in days.itertuples(name=None)
    }


def top_up_historical_prices(ledger: Ledger) -> HistoricalPriceUpdate:
    """Fetch the historical prices the folio's securities lack.

    A US dollar line the provider has no prices for, such as DLR.U.TO, is valued
    from its Canadian dollar twin, so the twin is fetched then too, over the same
    days: only for a line still without a price once the first fetch is done.
    """
    today = today_in_toronto()
    spans = price_spans(ledger, today=today)
    update = HistoricalPriceService.ensure_coverage(spans, today=today)
    lines = {symbol: twin for symbol in spans if (twin := cad_twin(symbol))}
    stored = HistoricalPriceService.stored(sorted(lines))
    twins = {
        twin: _widest(spans[symbol], spans.get(twin))
        for symbol, twin in lines.items()
        if not stored[symbol].dates
    }
    if not twins:
        return update
    return update.merged(HistoricalPriceService.ensure_coverage(twins, today=today))


def _widest(span: DateWindow, other: DateWindow | None) -> DateWindow:
    """Join two spans of days, from the earlier start to the later end."""
    if other is None:
        return span
    return DateWindow(start=min(span.start, other.start), end=max(span.end, other.end))


def latest_covered_day(ledger: Ledger, *, by: date) -> date:
    """Find the latest day, up to `by`, the store prices every held security on.

    A top-up that was skipped, throttled or failed leaves the store short of
    yesterday, and the latest close then is the last day it does cover.
    """
    stored = HistoricalPriceService.stored(ledger.held_symbols)
    through = [
        date.fromisoformat(prices.coverage.fetched_through)
        for prices in stored.values()
        if prices.coverage is not None
    ]
    return min([by, *through])
