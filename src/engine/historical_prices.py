"""The days each security needs historical prices for, and topping them up."""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

from domain import Column
from domain.dates import today_in_toronto
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
    """Fetch the historical prices the folio's securities lack."""
    today = today_in_toronto()
    return HistoricalPriceService.ensure_coverage(
        price_spans(ledger, today=today),
        today=today,
    )
