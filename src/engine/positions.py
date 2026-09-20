"""Turn the replayed frame plus live quotes into valued holdings."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd

from domain import Currency, Scope, WarningCode
from domain.numeric import ZERO, dec, safe_div
from engine.frames import (
    POOL_COLUMN,
    acb_summary_frame,
    acb_summary_frames_by_pool,
    scope_column,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from decimal import Decimal

    from engine.fx_rates import FxRates
    from services.quotes_service import Quote

# The currency the figures are *computed* in
ValuationCurrency = Currency | Literal["native"]


@dataclass(frozen=True)
class Holding:
    """One valued position in one pool.

    Attributes:
        symbol: Canonical security symbol.
        name: Short name from the quote, when one is cached.
        sector: Sector from the quote, when one is cached.
        pool: The account, account type or portfolio this is pooled over.
        currency: The currency the figures are expressed in.
        units: Units held after the last transaction.
        avg_cost: Cost base per unit.
        book_value: Cost base of the position.
        price: Last price, converted into `currency`.
        prev_close: Previous close, converted into `currency`.
        change: The day's per-share move.
        change_pct: `change / prev_close`
        day_pnl: `change * units`.
        day_pnl_pct: `day_pnl` over the **pool's** market value at yesterday's close.
        market_value: `price * units`.
        unrealized: `market_value - book_value`.
        unrealized_pct: `unrealized / book_value`.
        realized: Cumulative realized gain on this security in this pool.
        dividends: Cumulative dividends received from it in this pool.
        total_pnl: `unrealized + realized + dividends`, everything this holding
            has earned. None when it could not be priced, since a third of the
            sum would then be unknown.
        total_pnl_pct: `total_pnl / book_value`.
        total_pnl_cad: `total_pnl` in CAD
        market_value_cad: `market_value` in CAD
        weight_in_pool: Market value as a share of the displayed pool.
        weight_in_folio: Market value as a share of the whole portfolio.
        priced: Whether a usable price was found.
        flags: Diagnostics the replay raised against this symbol in this pool.
        closed: Whether this position has zero units in this pool.
    """

    symbol: str
    name: str | None
    pool: str
    currency: Currency
    units: Decimal
    avg_cost: Decimal
    book_value: Decimal
    # Defaults begin here: a field above this line is required of every holding.
    sector: str | None = None
    price: Decimal | None = None
    prev_close: Decimal | None = None
    change: Decimal | None = None
    change_pct: Decimal | None = None
    day_pnl: Decimal | None = None
    day_pnl_pct: Decimal | None = None
    market_value: Decimal | None = None
    unrealized: Decimal | None = None
    unrealized_pct: Decimal | None = None
    realized: Decimal = ZERO
    dividends: Decimal = ZERO
    total_pnl: Decimal | None = None
    total_pnl_pct: Decimal | None = None
    weight_in_pool: Decimal | None = None
    weight_in_folio: Decimal | None = None
    priced: bool = False
    flags: tuple[WarningCode, ...] = ()
    book_value_base: Decimal = ZERO
    market_value_base: Decimal | None = None
    day_pnl_base: Decimal | None = None
    unrealized_base: Decimal | None = None
    realized_base: Decimal = ZERO
    dividends_base: Decimal = ZERO
    total_pnl_base: Decimal | None = None
    total_pnl_cad: Decimal | None = None
    market_value_cad: Decimal | None = None
    closed: bool = False

    @property
    def closed_total(self) -> Decimal:
        """What a closed position earned over its whole life."""
        return self.realized + self.dividends


@dataclass(frozen=True)
class ClosedTotals:
    """Data about closed positions in a currency group.

    Attributes:
        count: How many closed positions were folded in.
        realized: Realized gain across them.
        dividends: Dividends received from them.
    """

    count: int
    realized: Decimal = ZERO
    dividends: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        """Everything the closed positions earned."""
        return self.realized + self.dividends


def summarize_closed(closed: Iterable[Holding]) -> ClosedTotals:
    """Aggregate closed positions into one earned-since-liquidation figure.

    Args:
        closed: The closed positions to fold together.

    Returns:
        Their combined realized gain and dividends.
    """
    members = list(closed)
    return ClosedTotals(
        count=len(members),
        realized=sum((member.realized for member in members), ZERO),
        dividends=sum((member.dividends for member in members), ZERO),
    )


@dataclass(frozen=True)
class CurrencyTotals:
    """One currency group's subtotals, in that currency.

    Some values are CAD normalized to properly calculate: `day_pnl_pct`,
    `weight_in_pool` and `weight_in_folio`

    Attributes:
        currency: The currency these holdings trade in.
        count: How many holdings are in the group.
        book: Cost base across the group.
        market: Market value across the group's **priced** holdings.
        day_pnl: The day's gain or loss across the priced holdings.
        unrealized: Unrealized gain across the priced holdings.
        realized: Realized gain across the group.
        dividends: Dividends received across the group.
        total_pnl: Everything the group has earned.
        day_pnl_pct: The group's share of the pool's day move.
        weight_in_pool: Market value as a share of the displayed pool.
        weight_in_folio: Market value as a share of the whole portfolio.
    """

    currency: Currency
    count: int
    book: Decimal = ZERO
    market: Decimal | None = None
    day_pnl: Decimal | None = None
    unrealized: Decimal | None = None
    realized: Decimal = ZERO
    dividends: Decimal = ZERO
    total_pnl: Decimal | None = None
    day_pnl_pct: Decimal | None = None
    weight_in_pool: Decimal | None = None
    weight_in_folio: Decimal | None = None

    @property
    def unrealized_pct(self) -> Decimal | None:
        """Unrealized gain over the group's own cost base."""
        if self.unrealized is None or not self.book:
            return None
        return safe_div(self.unrealized, self.book)

    @property
    def total_pnl_pct(self) -> Decimal | None:
        """Everything the group earned, over its own cost base.

        The cost base of anything sold has left `book` entirely, so this
        overstates the return of a group that has closed positions. It is still the
        right reading for a row meaning "what this currency did". BUT, inaccurate
        for a row meaning "what this pool returned": see `HoldingSet.return_on`.
        """
        if self.total_pnl is None or not self.book:
            return None
        return safe_div(self.total_pnl, self.book)


@dataclass(frozen=True)
class HoldingSet:
    """Every holding in one pool, with the totals they roll up to.

    The totals are in `base_currency`, never in a holding's own: adding a USD
    market value to a CAD one without converting would be nonsense. The
    per-currency subtotals in `by_currency` are the native ones.

    Attributes:
        holdings: The open positions, sorted by market value.
        scope: The grain the pool was read at.
        pool: The pool key, empty at portfolio grain.
        display_currency: The currency the figures are expressed in.
        base_currency: The currency the totals and ratios are expressed in.
        by_currency: Subtotals per currency group, each in its own currency.
        total_book: Cost base across every open holding, in `base_currency`.
        total_market: Market value across the **priced** holdings only.
        total_day_pnl: The day's gain or loss across the priced holdings.
        total_unrealized: Unrealized gain across the priced holdings.
        total_realized: Realized gain across every holding.
        total_dividends: Dividends received across every holding.
        total_pnl: Everything the pool has earned.
        total_pnl_cad: `total_pnl` in CAD.
        excluded: Positions the currency filter dropped from the pool.
        unpriced: Symbols held but not valued, excluded from every total.
        fx_rate: The USDCAD rate used to convert, when one was needed.
        fx_date: The date that rate is from, for disclosure.
    """

    holdings: list[Holding]
    scope: Scope
    pool: str
    display_currency: ValuationCurrency
    closed: tuple[Holding, ...] = ()
    base_currency: Currency = Currency.CAD
    by_currency: tuple[CurrencyTotals, ...] = ()
    total_book: Decimal = ZERO
    total_market: Decimal | None = None
    total_day_pnl: Decimal | None = None
    total_unrealized: Decimal | None = None
    total_realized: Decimal = ZERO
    total_dividends: Decimal = ZERO
    total_pnl: Decimal | None = None
    total_pnl_cad: Decimal | None = None
    excluded: int = 0
    unpriced: tuple[str, ...] = ()
    fx_rate: Decimal | None = None
    fx_date: str | None = None

    @property
    def mixed_currency(self) -> bool:
        """Whether more than one currency group is on show."""
        return len(self.by_currency) > 1

    @property
    def total_weight_in_folio(self) -> Decimal | None:
        """How much of everything owned this whole pool accounts for."""
        return _sum_ratios(holding.weight_in_folio for holding in self.holdings)

    @property
    def total_day_pnl_pct(self) -> Decimal | None:
        """The day's move over the pool's value at yesterday's close.

        Against `total_market - total_day_pnl`, not `total_market`: a return is
        measured from where the day started, which is also what makes the
        per-holding `day_pnl_pct` contributions sum to this figure.
        """
        if self.total_day_pnl is None or not self.total_market:
            return None
        return safe_div(self.total_day_pnl, self.total_market - self.total_day_pnl)

    @property
    def total_unrealized_pct(self) -> Decimal | None:
        """Unrealized gain over the pool's cost base."""
        if self.total_unrealized is None or not self.total_book:
            return None
        return safe_div(self.total_unrealized, self.total_book)

    @property
    def deposits_measurable(self) -> bool:
        """If holdings are excluded, net deposits are not measurable."""
        return self.excluded == 0

    def return_on(self, denominator: Decimal | None) -> Decimal | None:
        """Divide everything the pool earned by the Net Deposits provided.

        Against net deposits, not against book value. `total_pnl` counts what
        closed positions earned as well, and their cost base has left
        `total_book` entirely, so dividing by it overstates the return by the
        whole realized history of anything sold. Net deposits is what we want to
        compare against, and having the full replay lets us do that.

        Args:
            denominator: Net deposits to divide by, from
                `Flows.net_deposit_denominator`. None when the caller has no
                flows or the pool holds none of its own money.

        Returns:
            The ratio, or None when either side is missing or we are only seeing
            a subset of holdings.
        """
        if self.total_pnl_cad is None or not denominator:
            return None
        if not self.deposits_measurable:
            return None
        return safe_div(self.total_pnl_cad, denominator)

    @property
    def flags(self) -> tuple[WarningCode, ...]:
        """Every distinct diagnostic carried by the holdings shown."""
        seen: dict[WarningCode, None] = {}
        for holding in self.holdings:
            for code in holding.flags:
                seen.setdefault(code, None)
        return tuple(seen)


def scope_rows(frame: pd.DataFrame, scope: Scope, pool: str | None) -> pd.DataFrame:
    """Narrow the master frame to one pool.

    Args:
        frame: The master frame.
        scope: The grain to read at.
        pool: The account name or account type, ignored at portfolio grain.

    Returns:
        The rows belonging to that pool. Portfolio grain filters nothing.
    """
    column = POOL_COLUMN.get(scope)
    if column is None or not pool:
        return frame
    return frame[frame[column] == pool]


def held_symbols(frame: pd.DataFrame) -> list[str]:
    """Every symbol the folio still holds anywhere, at portfolio grain.

    Args:
        frame: The master frame.

    Returns:
        Canonical symbols with a non-zero portfolio-wide position, sorted.
    """
    return _open_symbols(acb_summary_frame(frame))


def _open_symbols(summary: pd.DataFrame) -> list[str]:
    """Read the still-held symbols off a portfolio-grain summary frame."""
    if summary.empty:
        return []
    column = scope_column(Scope.FOLIO, "Units")
    open_positions = summary[summary[column].fillna(0) != 0]
    return sorted({str(symbol) for symbol in open_positions["Symbol"] if symbol})


@dataclass(frozen=True)
class _Rollups:
    """One pool's derived inputs, read off the frame before anything is valued."""

    summary: pd.DataFrame
    flags: dict[str, tuple[WarningCode, ...]]
    income: dict[str, tuple[Decimal, Decimal]]


class FolioPositions:
    """One invocation's frame and quotes, with the per-pool rollups memoized.

    Valuing a pool means first deriving three things from the master frame: the
    summary frame, the flag rollup and the dividend rollup.

    Nothing here is cached beyond the object's own life.
    """

    def __init__(
        self,
        frame: pd.DataFrame,
        quotes: Mapping[str, Quote],
        fx: FxRates,
    ) -> None:
        """Hold one invocation's inputs.

        Args:
            frame: The replayed master frame.
            quotes: Canonical symbol to its cached quote.
            fx: Rates to convert a quote's currency into the display currency.
        """
        self.frame = frame
        self.quotes = quotes
        self.fx = fx
        self._rollups: dict[tuple[Scope, str], _Rollups] = {}
        self._market: Decimal | None = None
        self._valued = False

    def price(self, quotes: Mapping[str, Quote]) -> None:
        """Supply the quotes, once the symbols worth fetching are known.

        Args:
            quotes: Canonical symbol to its cached quote.
        """
        self.quotes = quotes
        # Anything already valued was valued without these.
        self._market = None
        self._valued = False

    def rollups(self, scope: Scope, pool: str | None) -> _Rollups:
        """Derive one pool's inputs, or hand back the ones already derived."""
        key = (scope, pool or "")
        held = self._rollups.get(key)
        if held is None:
            rows = scope_rows(self.frame, scope, pool)
            held = _Rollups(
                summary=acb_summary_frame(rows),
                flags=_flags_by_symbol(rows),
                income=_dividends_by_symbol(rows),
            )
            self._rollups[key] = held
        return held

    def prime(self, scope: Scope) -> None:
        """Derive every pool's summary at one grain in a single pass.

        We need to walk the frame anyway, lets do it all at once.

        Args:
            scope: The grain about to be reported pool by pool. Portfolio grain
                is a single pool and is ignored.
        """
        if scope is Scope.FOLIO:
            return
        by_pool = acb_summary_frames_by_pool(self.frame, scope)
        for pool, summary in by_pool.items():
            key = (scope, pool)
            if key in self._rollups:
                continue
            rows = scope_rows(self.frame, scope, pool)
            self._rollups[key] = _Rollups(
                summary=summary,
                flags=_flags_by_symbol(rows),
                income=_dividends_by_symbol(rows),
            )

    def held_symbols(self) -> list[str]:
        """Every symbol the folio still holds anywhere, at portfolio grain.

        Returns:
            Canonical symbols with a non-zero portfolio-wide position, sorted.
        """
        return _open_symbols(self.rollups(Scope.FOLIO, None).summary)

    def market_value(self) -> Decimal | None:
        """Total CAD market value of the whole portfolio.

        Needed even when a narrower pool is displayed, because `weight_in_folio`
        answers "how much of everything I own is this", which is the number that
        reveals real concentration.

        Returns:
            The portfolio's market value across priced positions, or None when
            nothing could be priced.
        """
        if not self._valued:
            self._market = self.holdings(scope=Scope.FOLIO).total_market
            self._valued = True
        return self._market

    def holdings(  # noqa: PLR0913
        self,
        *,
        scope: Scope,
        pool: str | None = None,
        currency: ValuationCurrency = Currency.CAD,
        folio_market: Decimal | None = None,
        sort: str | None = None,
        reverse: bool = False,
    ) -> HoldingSet:
        """Value every open position in one pool.

        Args:
            scope: The pool grain to read at.
            pool: The account name or account type, ignored at portfolio grain.
            currency: Currency to express the figures in, or `"native"`.
            folio_market: Total market value of the whole portfolio, in CAD.
            sort: The column to order by, or None for market value.
            reverse: Flip the sort's natural direction.

        Returns:
            The pool's holdings and totals.

        Raises:
            UnknownSortError: If `sort` is not a sortable measure.
        """
        derived = self.rollups(scope, pool)
        quotes = self.quotes
        fx = self.fx
        summary = derived.summary
        flags = derived.flags
        income = derived.income
        rate, rate_date = _latest_rate(fx)
        base = base_currency(currency)

        priced_base = ZERO
        excluded = 0
        partial: list[Holding] = []
        closed: list[Holding] = []
        context = _Context(quotes, flags, income, scope, pool or "", currency, fx)
        # Only `-c USD` ever hides a position, so only it has to keep count.
        filtering = currency is Currency.USD
        for record in summary.to_dict("records"):
            holding = _holding(record, context)
            if holding is None:
                # Count if we excluded anything due to currency filtering.
                if filtering:
                    excluded += _filtered_out(record, scope)
                continue
            if holding.closed:
                closed.append(holding)
                continue
            partial.append(holding)
            if holding.market_value_base is not None:
                priced_base += holding.market_value_base

        holdings = _with_shares(partial, priced_base, folio_market)
        if sort is None:
            holdings.sort(key=_by_value, reverse=True)
        else:
            holdings = sort_holdings(holdings, sort, reverse=reverse)
        closed.sort(key=lambda h: h.symbol)

        unpriced = tuple(h.symbol for h in holdings if not h.priced)
        any_priced = any(h.priced for h in holdings)
        closed_pnl = sum((h.realized_base + h.dividends_base for h in closed), ZERO)
        return HoldingSet(
            holdings=holdings,
            closed=tuple(closed),
            scope=scope,
            pool=pool or "",
            display_currency=currency,
            base_currency=base,
            by_currency=_by_currency(holdings, closed),
            total_book=sum((h.book_value_base for h in holdings), ZERO),
            total_market=priced_base if any_priced else None,
            total_day_pnl=(
                _sum_optional(h.day_pnl_base for h in holdings) if any_priced else None
            ),
            total_unrealized=(
                _sum_optional(h.unrealized_base for h in holdings)
                if any_priced
                else None
            ),
            total_realized=(
                sum((h.realized_base for h in holdings), ZERO)
                + sum((h.realized_base for h in closed), ZERO)
            ),
            total_dividends=(
                sum((h.dividends_base for h in holdings), ZERO)
                + sum((h.dividends_base for h in closed), ZERO)
            ),
            excluded=excluded,
            total_pnl_cad=_total_pnl_cad(holdings, closed, any_priced=any_priced),
            total_pnl=_total_pnl(
                _sum_optional(h.total_pnl_base for h in holdings),
                has_open=bool(holdings),
                closed_pnl=closed_pnl,
                any_priced=any_priced,
            ),
            unpriced=unpriced,
            fx_rate=rate,
            fx_date=rate_date,
        )


def base_currency(currency: ValuationCurrency) -> Currency:
    """Name the currency totals and ratios will be expressed in.

    Args:
        currency: The requested valuation currency.

    Returns:
        The currency to total and take ratios in.
    """
    return Currency.USD if currency == Currency.USD else Currency.CAD


def _by_currency(
    holdings: list[Holding],
    closed: list[Holding],
) -> tuple[CurrencyTotals, ...]:
    """Subtotal the holdings within each currency they trade in."""
    seen: dict[Currency, list[Holding]] = {}
    for holding in holdings:
        seen.setdefault(holding.currency, []).append(holding)
    closed_seen: dict[Currency, list[Holding]] = {}
    for holding in closed:
        closed_seen.setdefault(holding.currency, []).append(holding)

    ordered = sorted(
        set(seen) | set(closed_seen),
        key=lambda c: (c is not Currency.CAD, str(c)),
    )
    groups: list[CurrencyTotals] = []
    for currency in ordered:
        members = seen.get(currency, [])
        closed_members = closed_seen.get(currency, [])
        priced = any(member.priced for member in members)
        closed_pnl = sum(
            (m.realized + m.dividends for m in closed_members),
            ZERO,
        )
        groups.append(
            CurrencyTotals(
                currency=currency,
                count=len(members),
                book=sum((member.book_value for member in members), ZERO),
                market=(
                    _sum_optional(member.market_value for member in members)
                    if priced
                    else None
                ),
                day_pnl=(
                    _sum_optional(member.day_pnl for member in members)
                    if priced
                    else None
                ),
                unrealized=(
                    _sum_optional(member.unrealized for member in members)
                    if priced
                    else None
                ),
                realized=(
                    sum((member.realized for member in members), ZERO)
                    + sum((m.realized for m in closed_members), ZERO)
                ),
                dividends=(
                    sum((member.dividends for member in members), ZERO)
                    + sum((m.dividends for m in closed_members), ZERO)
                ),
                total_pnl=_total_pnl(
                    _sum_optional(member.total_pnl for member in members),
                    has_open=bool(members),
                    closed_pnl=closed_pnl,
                    any_priced=priced,
                ),
                # Summed, not divided
                day_pnl_pct=_sum_ratios(member.day_pnl_pct for member in members),
                weight_in_pool=_sum_ratios(member.weight_in_pool for member in members),
                weight_in_folio=_sum_ratios(
                    member.weight_in_folio for member in members
                ),
            ),
        )
    return tuple(groups)


def _total_pnl_cad(
    holdings: list[Holding],
    closed: list[Holding],
    *,
    any_priced: bool,
) -> Decimal | None:
    """Total what the pool earned in CAD, or None when part of it is unknown.

    Unlike the base total, this one refuses to be partial. It exists only as the
    numerator of a return against net deposits, and a sum quietly missing a
    holding would state a ratio based off an incomplete top half.

    Args:
        holdings: The pool's open positions.
        closed: Its sold-out ones, which always know what they banked.
        any_priced: Whether anything open could be valued at all.

    Returns:
        The CAD total, or None when it cannot be stated in full.
    """
    valued = [h for h in holdings if h.total_pnl_base is not None]
    if any(h.total_pnl_cad is None for h in (*valued, *closed)):
        return None
    return _total_pnl(
        _sum_optional(h.total_pnl_cad for h in holdings),
        has_open=bool(holdings),
        closed_pnl=_sum_optional(h.total_pnl_cad for h in closed),
        any_priced=any_priced,
    )


def _total_pnl(
    open_pnl: Decimal,
    *,
    has_open: bool,
    closed_pnl: Decimal,
    any_priced: bool,
) -> Decimal | None:
    """Combine open and closed positions into one total-earned figure."""
    if any_priced:
        return open_pnl + closed_pnl
    if not has_open:
        return closed_pnl
    return None


@dataclass(frozen=True)
class _Context:
    """Everything `_holding` needs that is the same for every row."""

    quotes: Mapping[str, Quote]
    flags: Mapping[str, tuple[WarningCode, ...]]
    income: Mapping[str, tuple[Decimal, Decimal]]
    scope: Scope
    pool: str
    currency: ValuationCurrency
    fx: FxRates


def _holding(record: Mapping[Any, Any], ctx: _Context) -> Holding | None:
    """Value one summary row, or None when it is not a position to show."""
    symbol = record.get("Symbol")
    if not isinstance(symbol, str) or not symbol:
        return None

    scope = ctx.scope
    held = _measure(record.get(scope_column(scope, "Units")))
    closed = held == ZERO

    native = _native_currency(record, scope)
    shown = native if ctx.currency == "native" else ctx.currency
    if ctx.currency == Currency.USD and native is not Currency.USD:
        # The USD columns are blank by design for a CAD-denominated holding
        return None

    common = base_currency(ctx.currency)
    book, avg = _cost(record, scope, shown, native)
    # The base book value is read off the frame, never converted
    book_base, _ = _cost(record, scope, common, native)

    # Realized gains and dividends are history, not valuation, so both are read
    # in whichever currency the frame already holds them in. Neither is ever
    # converted at today's rate.
    realized = _realized(record, scope, shown, native)
    realized_base = _realized(record, scope, common, native)
    dividends, dividends_base = _income(ctx.income, symbol, shown, common, native)

    if closed:
        return Holding(
            symbol=symbol,
            name=None,
            pool=ctx.pool,
            currency=shown,
            units=held,
            avg_cost=avg,
            book_value=book,
            realized=realized,
            dividends=dividends,
            book_value_base=book_base,
            realized_base=realized_base,
            dividends_base=dividends_base,
            total_pnl_cad=(
                realized_base + dividends_base
                if common is Currency.CAD
                else _cad_earned(record, ctx, symbol, native, held)
            ),
            flags=ctx.flags.get(symbol, ()),
            closed=True,
        )

    quote = ctx.quotes.get(symbol)
    price, prev_close = _converted_price(quote, shown, ctx.fx)
    price_base, prev_base = _converted_price(quote, common, ctx.fx)

    started = Holding(
        symbol=symbol,
        name=quote.name if quote else None,
        sector=quote.sector if quote else None,
        pool=ctx.pool,
        currency=shown,
        units=held,
        avg_cost=avg,
        book_value=book,
        realized=realized,
        dividends=dividends,
        book_value_base=book_base,
        realized_base=realized_base,
        dividends_base=dividends_base,
        flags=ctx.flags.get(symbol, ()),
    )
    if price is None:
        return started

    change = None if prev_close is None else price - prev_close
    market_value = price * held
    unrealized = market_value - book
    market_base = None if price_base is None else price_base * held
    change_base = (
        None if price_base is None or prev_base is None else (price_base - prev_base)
    )
    total = unrealized + realized + dividends
    total_base = (
        None if market_base is None else (market_base - book_base) + realized_base
    )
    return replace(
        started,
        price=price,
        prev_close=prev_close,
        change=change,
        change_pct=(
            None
            if change is None or prev_close is None
            else safe_div(change, prev_close)
        ),
        day_pnl=None if change is None else change * held,
        market_value=market_value,
        unrealized=unrealized,
        unrealized_pct=safe_div(unrealized, book) if book else None,
        total_pnl=total,
        total_pnl_pct=safe_div(total, book) if book else None,
        priced=True,
        market_value_base=market_base,
        market_value_cad=(
            market_base
            if common is Currency.CAD
            else _market_in_cad(quote, held, ctx.fx)
        ),
        day_pnl_base=None if change_base is None else change_base * held,
        unrealized_base=None if market_base is None else market_base - book_base,
        total_pnl_base=None if total_base is None else total_base + dividends_base,
        total_pnl_cad=(
            None
            if total_base is None
            else total_base + dividends_base
            if common is Currency.CAD
            else _cad_earned(record, ctx, symbol, native, held)
        ),
    )


def _market_in_cad(
    quote: Quote | None,
    held: Decimal,
    fx: FxRates,
) -> Decimal | None:
    """Value one open position in CAD, ignoring the display currency.

    Only reached when `-c USD` has made the base currency USD. `weight_in_folio`
    is a share of the whole portfolio, and the portfolio spans both currencies,
    so the ratio has to be taken in the one currency every holding can be
    expressed in.

    Args:
        quote: The holding's quote, if one was found.
        held: Units still held.
        fx: Rates to convert the quote's currency into CAD.

    Returns:
        The CAD market value, or None when the position could not be priced.
    """
    price, _ = _converted_price(quote, Currency.CAD, fx)
    return None if price is None else price * held


def _cad_earned(
    record: Mapping[Any, Any],
    ctx: _Context,
    symbol: str,
    native: Currency,
    held: Decimal,
) -> Decimal | None:
    """Read everything one holding earned, in CAD, ignoring the display currency.

    Only reached when `-c USD` has made the base currency USD. Every other
    figure on the row is then USD by request, but the deposit-based return
    divides by net *CAD* deposits, so its numerator has to be read from the
    frame's CAD columns rather than converted back at today's rate. Realized
    gains and dividends therefore keep the historical rates they were booked
    at, exactly as they do everywhere else.

    Args:
        record: The holding's row from the summary frame.
        ctx: The valuation context, for the quotes, income rollup and FX table.
        symbol: Canonical security symbol.
        native: The currency the holding trades in.
        held: Units still held, zero for a closed position.

    Returns:
        The CAD total, or None when an open position could not be priced.
    """
    book, _ = _cost(record, ctx.scope, Currency.CAD, native)
    realized = _realized(record, ctx.scope, Currency.CAD, native)
    _, dividends = _income(ctx.income, symbol, Currency.CAD, Currency.CAD, native)
    banked = realized + dividends
    if held == ZERO:
        return banked

    price, _ = _converted_price(ctx.quotes.get(symbol), Currency.CAD, ctx.fx)
    if price is None:
        return None
    return (price * held - book) + banked


def _filtered_out(record: Mapping[Any, Any], scope: Scope) -> int:
    """Say whether `-c USD` is why `_holding` refused a row.

    Args:
        record: The summary row `_holding` refused.
        scope: The grain the row was read at.

    Returns:
        1 when the filter dropped a real position, 0 otherwise.
    """
    symbol = record.get("Symbol")
    if not isinstance(symbol, str) or not symbol:
        # Not a position at all, so nothing was hidden.
        return 0
    return int(_native_currency(record, scope) is not Currency.USD)


def _with_shares(
    holdings: list[Holding],
    pool_market: Decimal,
    folio_market: Decimal | None,
) -> list[Holding]:
    """Fill the three figures that need a pool-level denominator.

    `day_pnl_pct`, `weight_in_pool` and `weight_in_folio` all divide by a total
    that is only known once every holding has been valued, so they are a second
    pass rather than part of `_holding`. `day_pnl_pct` divides by the **pool's**
    value, not the position's own: it measures a holding's contribution to the
    pool's day move, so a large position drifting 1% outweighs a tiny one
    jumping 8%. Dividing by the position's own value instead would cancel the
    units and collapse the column into `change_pct`.

    **All three are taken on the base-currency figures**, whatever currency the
    row itself is reported in. A weight answers "how much of what I own is
    this", which spans currencies by definition, so a USD holding's 5% and a CAD
    holding's 5% have to mean the same thing.

    Both denominators count **priced** positions only, so an unpriced holding
    cannot silently distort the shares of the ones that did price.
    """
    pool_cad = _sum_optional(holding.market_value_cad for holding in holdings)
    folio_total = folio_market if folio_market is not None else pool_cad
    pool_prior = sum(
        (
            holding.market_value_base - (holding.day_pnl_base or ZERO)
            for holding in holdings
            if holding.market_value_base is not None
        ),
        ZERO,
    )
    filled: list[Holding] = []
    for holding in holdings:
        if holding.market_value_base is None:
            filled.append(holding)
            continue
        filled.append(
            replace(
                holding,
                day_pnl_pct=(
                    None
                    if holding.day_pnl_base is None
                    else safe_div(holding.day_pnl_base, pool_prior)
                ),
                weight_in_pool=safe_div(holding.market_value_base, pool_market),
                weight_in_folio=(
                    None
                    if holding.market_value_cad is None
                    else safe_div(holding.market_value_cad, folio_total)
                ),
            ),
        )
    return filled


def _suffix(shown: Currency, native: Currency) -> str:
    """Pick the column variant a currency should be read from.

    The `_USD` family is populated only for USD-denominated holdings, so it is
    the right one exactly when both the holding and the request are USD.
    """
    return "_USD" if shown is Currency.USD and native is Currency.USD else ""


def _cost(
    record: Mapping[Any, Any],
    scope: Scope,
    shown: Currency,
    native: Currency,
) -> tuple[Decimal, Decimal]:
    """Read book value and average cost in the currency being displayed."""
    suffix = _suffix(shown, native)
    book = _measure(record.get(scope_column(scope, f"ACB{suffix}")))
    avg = _measure(record.get(scope_column(scope, f"Avg{suffix}")))
    return book, avg


def _realized(
    record: Mapping[Any, Any],
    scope: Scope,
    shown: Currency,
    native: Currency,
) -> Decimal:
    """Read the cumulative realized gain in the currency being displayed."""
    return _measure(record.get(scope_column(scope, f"Gain{_suffix(shown, native)}")))


def _income(
    dividends: Mapping[str, tuple[Decimal, Decimal]],
    symbol: str,
    shown: Currency,
    base: Currency,
    native: Currency,
) -> tuple[Decimal, Decimal]:
    """Read one security's dividends, in the shown and the base currency."""
    cad, usd = dividends.get(symbol, (ZERO, ZERO))
    return (usd if _suffix(shown, native) else cad), (
        usd if _suffix(base, native) else cad
    )


def _dividends_by_symbol(rows: pd.DataFrame) -> dict[str, tuple[Decimal, Decimal]]:
    """Total the dividends each security paid, within this pool.

    This is summed here deliberately, as it is a plain sum of a column on every
    row scoped to a particular security in this pool.

    Args:
        rows: The pool's rows from the master frame.

    Returns:
        Each symbol mapped to `(CAD, USD)` totals. The USD figure is blank for
        a CAD-denominated holding, matching the frame's own convention.
    """
    if rows.empty or "Dividend" not in rows.columns:
        return {}
    # Filter `paying` to only rows with actual dividends first.
    paying = rows[rows["Dividend"].notna() | rows["Dividend_USD"].notna()]
    totals: dict[str, tuple[Decimal, Decimal]] = {}
    for symbol, cad, usd in zip(
        paying["Symbol"].to_numpy(),
        paying["Dividend"].to_numpy(),
        paying["Dividend_USD"].to_numpy(),
        strict=True,
    ):
        if not isinstance(symbol, str) or not symbol:
            continue
        running = totals.get(symbol, (ZERO, ZERO))
        totals[symbol] = (running[0] + _measure(cad), running[1] + _measure(usd))
    return totals


def _converted_price(
    quote: Quote | None,
    shown: Currency,
    fx: FxRates,
) -> tuple[Decimal | None, Decimal | None]:
    """Convert a quote into the display currency at **today's** rate.

    Returns:
        The price and previous close in `shown`, or `(None, None)` when the
        quote is missing, unpriced, or in a currency that cannot be converted.
    """
    if quote is None or not quote.priced or quote.price is None:
        return None, None
    if quote.currency is None or quote.currency is shown:
        return quote.price, quote.prev_close

    today = _today(fx)
    try:
        price = fx.to_cad(quote.price, today, quote.currency)
        prev = (
            None
            if quote.prev_close is None
            else fx.to_cad(quote.prev_close, today, quote.currency)
        )
    except (ValueError, RuntimeError):
        # A currency the FX table cannot reach is an unpriced position, not a
        # crashed command.
        return None, None

    if shown is Currency.CAD:
        return price.value, None if prev is None else prev.value

    from_cad = fx.from_cad(price.value, today, shown)
    prev_usd = None if prev is None else fx.from_cad(prev.value, today, shown).value
    return from_cad.value, prev_usd


def _native_currency(record: Mapping[str, object], scope: Scope) -> Currency:
    """Infer the currency a holding trades in, off its USD columns."""
    usd: Any = record.get(scope_column(scope, "ACB_USD"))
    return Currency.CAD if usd is None or pd.isna(usd) else Currency.USD


def _flags_by_symbol(rows: pd.DataFrame) -> dict[str, tuple[WarningCode, ...]]:
    """Roll the frame's `Flags` column up per symbol, within this pool."""
    if rows.empty or "Flags" not in rows.columns:
        return {}
    grouped: dict[str, dict[WarningCode, None]] = {}
    for symbol, flags in zip(
        rows["Symbol"].to_numpy(),
        rows["Flags"].to_numpy(),
        strict=True,
    ):
        if not isinstance(symbol, str) or not flags or pd.isna(flags):
            continue
        for token in str(flags).split(","):
            try:
                code = WarningCode(token.strip())
            except ValueError:
                continue
            grouped.setdefault(symbol, {}).setdefault(code, None)
    return {symbol: tuple(codes) for symbol, codes in grouped.items()}


def _latest_rate(fx: FxRates) -> tuple[Decimal | None, str | None]:
    """Find the most recent USDCAD rate held, to disclose beside a valuation."""
    found = fx.as_of(_today(fx))
    if found is None:
        return None, None
    rate_date, rate = found
    return rate, rate_date


def _today(fx: FxRates) -> str:
    """Pick the date to value at: the latest rate held, which `as_of` carries back."""
    _, last = fx.coverage
    return last or "9999-12-31"


def _measure(value: Any) -> Decimal:  # noqa: ANN401 - a frame cell is untyped
    """Read one measure off the frame as a Decimal, treating a blank as zero."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ZERO
    return dec(value)


def _sum_optional(values: Iterable[Decimal | None]) -> Decimal:
    """Total a series of optional figures, skipping the blanks."""
    return sum((value for value in values if value is not None), ZERO)


def _sum_ratios(values: Iterable[Decimal | None]) -> Decimal | None:
    """Total a series of optional ratios, or None when none of them exist.

    Unlike `_sum_optional`, an empty series reads as *unknown* rather than as
    zero. An unpriced group carries no weight and no day move, and reporting
    those as `0.00%` would claim it contributed nothing rather than admitting
    it could not be measured.
    """
    present = [value for value in values if value is not None]
    return sum(present, ZERO) if present else None


# sort key to table heading to sort by, and whether the value is text
_SORT_KEYS: dict[str, tuple[str, bool]] = {
    "symbol": ("symbol", True),
    "name": ("name", True),
    "units": ("units", False),
    "avg": ("avg_cost", False),
    "last": ("price", False),
    "price": ("price", False),
    "change": ("change", False),
    "change%": ("change_pct", False),
    "pnl": ("day_pnl", False),
    "pnl%": ("day_pnl_pct", False),
    "unreal": ("unrealized", False),
    "unreal%": ("unrealized_pct", False),
    "realized": ("realized", False),
    "divs": ("dividends", False),
    "dividends": ("dividends", False),
    "total": ("total_pnl", False),
    "total%": ("total_pnl_pct", False),
    "book": ("book_value_base", False),
    "market": ("market_value_base", False),
    "wt%": ("weight_in_pool", False),
    "folio%": ("weight_in_folio", False),
}

SORT_NAMES: tuple[str, ...] = tuple(dict.fromkeys(_SORT_KEYS))


class UnknownSortError(ValueError):
    """A `--sort` argument naming something that is not a column."""

    def __init__(self, name: str) -> None:
        """Report the bad input, and present the valid options."""
        self.name = name
        super().__init__(
            f"Cannot sort by '{name}'. Try one of: {', '.join(SORT_NAMES)}.",
        )


def sort_holdings(
    holdings: list[Holding],
    name: str,
    *,
    reverse: bool = False,
) -> list[Holding]:
    """Order holdings by one measure.

    Numbers sort largest-first and text A-to-Z, reverse flips the order.

    Args:
        holdings: The positions to order.
        name: A name from `SORT_NAMES`, matched case-insensitively.
        reverse: Flip the natural direction.

    Returns:
        A new, ordered list.

    Raises:
        UnknownSortError: If `name` is not a sortable measure.
    """
    key = _SORT_KEYS.get(name.strip().lower())
    if key is None:
        raise UnknownSortError(name)
    attribute, is_text = key

    if is_text:
        return sorted(
            holdings,
            key=lambda h: str(getattr(h, attribute) or "").upper(),
            reverse=reverse,
        )

    # An unpriced holding will always sink to the bottom.
    valued = [h for h in holdings if getattr(h, attribute) is not None]
    blank = [h for h in holdings if getattr(h, attribute) is None]
    valued.sort(key=lambda h: getattr(h, attribute), reverse=not reverse)
    return valued + blank


def _by_value(holding: Holding) -> tuple[int, Decimal]:
    """Return sort key for the provided holding.

    Ordered on the base-currency figure so a mixed-currency pool sorts by what
    each position is actually worth, not by the size of its own currency unit.
    """
    if holding.market_value_base is None:
        return (0, ZERO)
    return (1, holding.market_value_base)
