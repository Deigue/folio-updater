"""Gain since buy and Gain%: potential gains due to a buy.

The replay never sees a price, so the saved ledger leaves a BUY row's `Gain` blank.
This fills it from the quotes on hand, and works out `GainPct` beside every `Gain`,
without replaying and without saving anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from math import prod
from typing import TYPE_CHECKING

from domain import Action, Column, Currency, Grain
from engine.frames import grain_column
from engine.holdings import converted_price

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pandas as pd

    from engine.fx_rates import FxRates
    from services.quotes_service import Quote

# A provider split this close to a folio SPLIT row is effectively the same split.
_SAME_SPLIT_WITHIN = timedelta(days=14)

_SYMBOL = str(Column.Ledger.SYMBOL)
_ACTION = str(Column.Txn.ACTION)
_TXN_DATE = str(Column.Txn.TXN_DATE)
_UNITS = str(Column.Txn.UNITS)
_PRICE = str(Column.Txn.PRICE)


@dataclass(frozen=True)
class _Split:
    """One split of a security, from either source.

    Attributes:
        date: The day it is dated, `YYYY-MM-DD`.
        ratio: Units after per unit before.
        covers_same_day: Whether a buy on that day is split too. The replay
            applies a day's buys before its splits, so a folio row does. The
            provider dates a split by the day the new price starts, so it does not.
    """

    date: str
    ratio: float
    covers_same_day: bool

    def applies_to(self, bought: str) -> bool:
        """Whether a buy on this day came before the split."""
        return bought < self.date or (self.covers_same_day and bought == self.date)


def with_gains_since_buy(
    frame: pd.DataFrame,
    quotes: Mapping[str, Quote],
    fx: FxRates,
) -> pd.DataFrame:
    """Add the gains since buy to the given dataframe using quotes.

    Args:
        frame: The whole ledger, since a split reported by any account counts.
        quotes: Canonical symbol to its quote. A buy with no price stays blank.
        fx: Rates to convert a quote at today's rate.

    Returns:
        A copy with Gain since buy in each BUY row's `Gain`, and a `GainPct` at
        every grain: against the buy's cost, or the cost a sale removed, and blank
        where a row removed no cost.
    """
    rows = frame.copy()
    buys = rows[_ACTION] == str(Action.BUY)
    sells = rows[_ACTION] == str(Action.SELL)
    units = _units_today(rows, buys, quotes)
    worth = {
        "": units * _prices(rows, quotes, Currency.CAD, fx),
        "_USD": units * _prices(rows, quotes, Currency.USD, fx),
    }
    for grain in Grain:
        for suffix, worth_today in worth.items():
            delta = rows[grain_column(grain, f"Delta{suffix}")].astype(float)
            gain = rows[grain_column(grain, f"Gain{suffix}")].astype(float)
            gain = gain.where(~buys, worth_today - delta)
            cost = delta.where(buys, -delta).where(buys | sells)
            rows[grain_column(grain, f"Gain{suffix}")] = gain
            rows[grain_column(grain, f"GainPct{suffix}")] = (gain / cost).where(
                cost > 0,
            )
    return rows


def _units_today(
    rows: pd.DataFrame,
    buys: pd.Series,
    quotes: Mapping[str, Quote],
) -> pd.Series:
    """Work out how many units each buy's are today, blank on every other row."""
    splits = _splits_by_symbol(rows, quotes)
    bought = rows[buys]
    ratios = [
        prod(
            split.ratio
            for split in splits.get(symbol, ())
            if split.applies_to(str(when))
        )
        for symbol, when in zip(bought[_SYMBOL], bought[_TXN_DATE], strict=True)
    ]
    return (bought[_UNITS].abs() * ratios).reindex(rows.index)


def _splits_by_symbol(
    rows: pd.DataFrame,
    quotes: Mapping[str, Quote],
) -> dict[str, list[_Split]]:
    """List each security's splits: the folio's, then the provider's it lacks."""
    recorded = rows[
        (rows[_ACTION] == str(Action.SPLIT))
        & rows[_SYMBOL].notna()
        & (rows[_PRICE] > 0)
        & (rows[_UNITS] > 0)
    ]
    splits: dict[str, list[_Split]] = {}
    # Each account that held through a split reports it: one split, not several.
    for symbol, when, ratio in {
        (row[_SYMBOL], row[_TXN_DATE], row[_UNITS] / row[_PRICE])
        for row in recorded.to_dict("records")
    }:
        splits.setdefault(str(symbol), []).append(
            _Split(str(when), ratio, covers_same_day=True),
        )

    for symbol, quote in quotes.items():
        folio = splits.get(symbol, [])
        splits[symbol] = folio + [
            _Split(split.date, float(split.ratio), covers_same_day=False)
            for split in quote.splits or ()
            if not any(_same_split(split.date, known.date) for known in folio)
        ]
    return splits


def _same_split(provider: str, folio: str) -> bool:
    """Whether a provider's split and a folio SPLIT row are close enough to match."""
    apart = date.fromisoformat(provider) - date.fromisoformat(folio)
    return abs(apart) <= _SAME_SPLIT_WITHIN


def _prices(
    rows: pd.DataFrame,
    quotes: Mapping[str, Quote],
    currency: Currency,
    fx: FxRates,
) -> pd.Series:
    """Read each row's price today in one currency, blank where there is none."""
    prices = {
        symbol: float(price)
        for symbol, quote in quotes.items()
        if (price := converted_price(quote, currency, fx)[0]) is not None
    }
    return rows[_SYMBOL].map(prices).astype(float)
