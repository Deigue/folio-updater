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

from domain import AccountType, Column, Currency, Grain
from domain.dates import weekday_on_or_before
from domain.numeric import ONE, ZERO, q2
from domain.symbols import cad_twin
from engine.accounts import FOLIO, Pool, account_type_of
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

    @property
    def empty(self) -> bool:
        """Say whether it holds nothing: no holding and not a cent of cash."""
        return not self.holdings and not any(
            q2(amount) for amount in self.cash.values()
        )

    @property
    def currencies(self) -> frozenset[Currency]:
        """The currencies it holds anything in, holdings or at least a cent of cash."""
        held = {held.currency for held in self.holdings}
        return frozenset(
            held | {kind for kind, amount in self.cash.items() if q2(amount)},
        )

    def held(self, symbol: str) -> DatedHolding | None:
        """Find one open holding by symbol, or None when the pool holds none."""
        return next((held for held in self.holdings if held.symbol == symbol), None)

    def holding_value(self, held: DatedHolding, into: Currency) -> Decimal | None:
        """Convert one holding's market value at the date's rate, None when unpriced."""
        value = held.market_value
        return None if value is None else self._convert(value, held.currency, into)

    def line_holdings(self, currency: Currency) -> Decimal:
        """Sum the priced holdings trading in one currency, left in it."""
        return sum(
            (
                value
                for held in self.holdings
                if held.currency is currency
                and (value := held.market_value) is not None
            ),
            ZERO,
        )

    def line_cash(self, currency: Currency) -> Decimal:
        """Read the cash held in one currency."""
        return self.cash.get(currency, ZERO)

    def line_value(self, currency: Currency) -> Decimal:
        """Add one currency line's priced holdings to its cash, unconverted."""
        return self.line_holdings(currency) + self.line_cash(currency)

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

    def rows(self, scope: Pool = FOLIO, *, by_type: bool = False) -> PoolRows | None:
        """List the pools a report shows within one pool, and that pool as the total.

        Args:
            scope: The pool to report: the folio, an account type or one account.
            by_type: List type-grain pools instead of accounts.

        Returns:
            The rows that hold anything, in account type then name order, or
            None when the scope held nothing by the date.
        """
        total = self.pools.get(scope)
        if total is None:
            return None
        grain = Grain.TYPE if by_type else Grain.ACCOUNT
        members = sorted(
            (
                dated
                for pool, dated in self.pools.items()
                if pool.grain is grain and _within(pool, scope) and not dated.empty
            ),
            key=lambda dated: _listing_order(dated.pool),
        )
        return PoolRows(tuple(members), total)


@dataclass(frozen=True)
class PoolRows:
    """The pools one report lists, and the pool it totals.

    Attributes:
        rows: The accounts, or the type-grain pools, it lists.
        total: The pool they belong to.
    """

    rows: tuple[DatedPool, ...]
    total: DatedPool

    def in_transit(self, into: Currency) -> Decimal:
        """Sum what transfers had taken out of one account and not yet delivered.

        It belongs to no account but stays in every pool holding both ends, so
        it is the gap between the total and the rows added up.
        """
        listed = sum((dated.market_value(into) for dated in self.rows), ZERO)
        return self.total.market_value(into) - listed

    def holdings(self, symbol: str) -> list[tuple[DatedPool, DatedHolding]]:
        """Pair each listed pool holding one security with its holding of it."""
        return [
            (dated, held)
            for dated in self.rows
            if (held := dated.held(symbol)) is not None
        ]


def ranged_pools(
    valuations: Sequence[DatedValuation],
    scope: Pool,
    *,
    by_type: bool = False,
) -> list[Pool]:
    """List every pool a range shows: each holding anything at one of its dates.

    Returns:
        The accounts, or type-grain pools, in account type then name order.
    """
    seen = {
        dated.pool
        for valued in valuations
        if (rows := valued.rows(scope, by_type=by_type)) is not None
        for dated in rows.rows
    }
    return sorted(seen, key=_listing_order)


def _listing_order(pool: Pool) -> tuple[str, str]:
    """Order pools by account type, then by name."""
    return str(account_type_of(pool)), pool.name


def _within(pool: Pool, scope: Pool) -> bool:
    """Say whether a pool sits inside another: an account inside its type."""
    if scope.grain is Grain.FOLIO or pool == scope:
        return True
    if scope.grain is Grain.TYPE and pool.grain is Grain.ACCOUNT:
        return account_type_of(pool) is AccountType(scope.name)
    return False


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
