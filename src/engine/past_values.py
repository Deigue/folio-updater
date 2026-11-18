"""Every pool's market value on past valuation dates, from historical prices.

What a pool held on a day is read off the ledger: each holding's units after its
last row on or before that day, and the cash every row up to it moved. Both
follow transaction date, as a broker's own market value does. Nothing is stored:
the figures are worked out again on every call.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd

from domain import Column, Currency, Grain
from domain.dates import weekday_on_or_before
from domain.numeric import ONE, ZERO
from domain.symbols import cad_twin
from engine.accounts import FOLIO, Pool
from engine.frames import CASH_MOVES, POOL_COLUMN, grain_column, replay_ordered
from engine.fx_rates import load_fx_rates
from services.historical_prices import HistoricalPriceService

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from engine.fx_rates import FxRates
    from engine.ledger import Ledger
    from services.historical_prices import StoredPrices

_SYMBOL = str(Column.Ledger.SYMBOL)
_TXN_DATE = str(Column.Txn.TXN_DATE)
_CURRENCY = str(Column.Txn.CURRENCY)
_POOL = "_pool"


@dataclass(frozen=True)
class DatedHolding:
    """One holding in one pool on a valuation date.

    Attributes:
        symbol: The security.
        units: Units held after the last row on or before the date.
        currency: The currency it trades in.
        price: Its historical price on the date, or None when it has none.
        twin: The Canadian dollar line its price was converted from, when it
            has no prices of its own.
    """

    symbol: str
    units: Decimal
    currency: Currency
    price: Decimal | None
    twin: str | None = None

    @property
    def market_value(self) -> Decimal | None:
        """Units times price, in the holding's own currency, or None when unpriced."""
        return None if self.price is None else self.units * self.price


@dataclass(frozen=True)
class DatedPool:
    """One pool on a valuation date: its open holdings and its cash.

    Attributes:
        pool: The pool.
        holdings: Its open holdings, sorted by symbol.
        cash: Its cash per currency.
        usdcad: The USD/CAD rate in force on the date.
    """

    pool: Pool
    holdings: tuple[DatedHolding, ...]
    cash: Mapping[Currency, Decimal]
    usdcad: Decimal

    @property
    def unpriced(self) -> tuple[DatedHolding, ...]:
        """The holdings with no price on the date, left out of every value."""
        return tuple(held for held in self.holdings if held.price is None)

    def holdings_value(self, into: Currency) -> Decimal:
        """Sum the priced holdings' market value, converted at the date's rate."""
        return sum(
            (
                self._convert(value, held.currency, into)
                for held in self.holdings
                if (value := held.market_value) is not None
            ),
            ZERO,
        )

    def cash_value(self, into: Currency) -> Decimal:
        """Sum the cash in every currency, converted at the date's rate."""
        return sum(
            (self._convert(amount, held, into) for held, amount in self.cash.items()),
            ZERO,
        )

    def market_value(self, into: Currency) -> Decimal:
        """Add the priced holdings to the cash, converted at the date's rate."""
        return self.holdings_value(into) + self.cash_value(into)

    def _convert(self, amount: Decimal, frm: Currency, into: Currency) -> Decimal:
        """Convert between CAD and USD at the date's rate."""
        if frm is into:
            return amount
        return amount * self.usdcad if into is Currency.CAD else amount / self.usdcad


@dataclass(frozen=True)
class DatedValuation:
    """Every pool, at every grain, on one valuation date.

    Attributes:
        asked: The day asked for.
        on: The valuation date: the day asked, or the Friday before a weekend.
        rate_date: The day the USD/CAD rate used was published.
        pools: Every pool holding anything by the date.
    """

    asked: date
    on: date
    rate_date: str
    pools: Mapping[Pool, DatedPool]

    def in_transit(self, into: Currency) -> Decimal:
        """Sum what transfers had taken out of one account and not yet delivered.

        It belongs to no account but stays in the folio, so it is the gap
        between the folio's value and its accounts' values added up.
        """
        folio = self.pools.get(FOLIO)
        if folio is None:
            return ZERO
        accounts = sum(
            (
                dated.market_value(into)
                for pool, dated in self.pools.items()
                if pool.grain is Grain.ACCOUNT
            ),
            ZERO,
        )
        return folio.market_value(into) - accounts


def value_pools(ledger: Ledger, days: Sequence[date]) -> list[DatedValuation]:
    """Value every pool on each day asked, at its valuation date.

    Returns:
        One valuation per day, in the order asked.
    """
    rows = replay_ordered(ledger.frame)
    symbols = sorted(rows[_SYMBOL].dropna().unique())
    currencies = {
        str(symbol): Currency(currency)
        for symbol, currency in rows.dropna(subset=[_SYMBOL])
        .groupby(_SYMBOL)[_CURRENCY]
        .first()
        .items()
    }
    twins = {twin for symbol in symbols if (twin := cad_twin(symbol)) is not None}
    prices = HistoricalPriceService.stored(sorted({*symbols, *twins}))
    fx = load_fx_rates()
    return [
        _valuation(rows, day, currencies=currencies, prices=prices, fx=fx)
        for day in days
    ]


def _valuation(
    rows: pd.DataFrame,
    asked: date,
    *,
    currencies: Mapping[str, Currency],
    prices: Mapping[str, StoredPrices],
    fx: FxRates,
) -> DatedValuation:
    """Value every pool at the valuation date for one day asked."""
    on = weekday_on_or_before(asked)
    rate = fx.to_cad(ONE, on.isoformat(), Currency.USD)
    upto = rows[rows[_TXN_DATE] <= on.isoformat()]
    pools: dict[Pool, DatedPool] = {}
    for grain in Grain:
        named = upto.assign(**{_POOL: _pool_names(upto, grain)})
        held = _units(named, grain)
        for name, cash in _cash(named, grain).items():
            pool = Pool(grain, name) if grain is not Grain.FOLIO else FOLIO
            pools[pool] = DatedPool(
                pool=pool,
                holdings=tuple(
                    _holding(symbol, units, currencies[symbol], on, prices, rate.rate)
                    for symbol, units in held.get(name, ())
                ),
                cash=cash,
                usdcad=rate.rate,
            )
    return DatedValuation(asked=asked, on=on, rate_date=rate.rate_date, pools=pools)


def _holding(  # noqa: PLR0913, PLR0917 - one holding's every fact, all needed
    symbol: str,
    units: Decimal,
    currency: Currency,
    on: date,
    prices: Mapping[str, StoredPrices],
    usdcad: Decimal,
) -> DatedHolding:
    """Price a holding on a date, from its Canadian dollar twin when it has none."""
    if (own := prices[symbol].on(on)) is not None:
        return DatedHolding(symbol, units, currency, own)
    twin = cad_twin(symbol)
    if twin is None or (close := prices[twin].on(on)) is None:
        return DatedHolding(symbol, units, currency, None)
    return DatedHolding(symbol, units, currency, close / usdcad, twin)


def _pool_names(rows: pd.DataFrame, grain: Grain) -> pd.Series:
    """Name the pool each row belongs to at one grain."""
    if grain is Grain.FOLIO:
        return pd.Series(FOLIO.name, index=rows.index)
    return rows[POOL_COLUMN[grain]].astype(str)


def _units(
    rows: pd.DataFrame,
    grain: Grain,
) -> dict[str, list[tuple[str, Decimal]]]:
    """Each pool's open holdings: units after each symbol's last row, by symbol."""
    tracked = rows.dropna(subset=[_SYMBOL])
    last = tracked.groupby([_POOL, _SYMBOL])[grain_column(grain, "Units")].last()
    held: dict[str, list[tuple[str, Decimal]]] = {}
    for (pool, symbol), units in sorted(last.items()):  # ty: ignore[not-iterable]
        if units:
            held.setdefault(str(pool), []).append((str(symbol), _decimal(units)))
    return held


def _cash(rows: pd.DataFrame, grain: Grain) -> dict[str, dict[Currency, Decimal]]:
    """Each pool's cash per currency: what every row up to the date moved."""
    columns = {
        currency: grain_column(grain, suffix) for currency, suffix in CASH_MOVES.items()
    }
    return {
        str(pool): {
            currency: sum((_decimal(value) for value in group[column].dropna()), ZERO)
            for currency, column in columns.items()
        }
        for pool, group in rows.groupby(_POOL)
    }


def _decimal(value: float) -> Decimal:
    """Read a frame number back exactly as it was written."""
    return Decimal(str(value))
