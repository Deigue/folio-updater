"""Tests for one security's moves and its holdings across the folio."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from domain import Currency, PriceRange, QuoteStatus
from engine.cache import build
from engine.panels import FolioValuation, type_view
from engine.performance import RangeMove, price_moves
from engine.ticker import (
    FirstBuy,
    PerformanceRow,
    UnknownMatrixSortError,
    performance_rows,
    sort_performance,
    symbol_position,
)
from services.price_history import PricePoint, RangeHistory
from services.quotes_service import Quote

from .helpers.seed import seed_fx, seed_transaction

if TYPE_CHECKING:
    from .test_types import TempContext

FX = {f"2025-08-{day}": "1.25" for day in range(14, 21)}


def _seed_two_types() -> None:
    """Buy one symbol at two prices in two types, and round-trip it in a third."""
    seed_fx(FX)
    for account, amount, units, day in (
        ("WS-PERSONAL", "-1000", "10", "2025-08-14"),
        ("WS-TFSA", "-1500", "10", "2025-08-15"),
        ("QT-RRSP", "-800", "5", "2025-08-18"),
    ):
        seed_transaction(account=account, amount=amount, units=units, date=day)
    seed_transaction(
        action="SELL",
        account="QT-RRSP",
        amount="1100",
        units="-5",
        date="2025-08-19",
    )


def _valuation() -> FolioValuation:
    cached = build()
    assert cached.result is not None
    return FolioValuation.build(cached.frame, cached.result, currency="native")


def test_the_total_is_the_portfolio_grain_not_the_sum_of_the_lines(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        _seed_two_types()
        valuation = _valuation()

        position = symbol_position(valuation, "TESTTKR")

        assert position is not None
        lines = {line.view.pool: line.holding for line in position.lines}
        # The open pools' units add up...
        assert (
            position.total.units == lines["NON_REGISTERED"].units + lines["TFSA"].units
        )
        # ...but pooled across every account, the RRSP's round trip moved the
        # portfolio's average, which no combination of the lines reproduces.
        # A USD holding valued natively reads the USD family of columns.
        frame = valuation.frame
        folio_avg = frame[frame["Symbol"] == "TESTTKR"]["FolioAvg_USD"].iloc[-1]
        assert position.total.avg_cost == Decimal(str(folio_avg))
        assert position.total.avg_cost not in {
            lines["NON_REGISTERED"].avg_cost,
            lines["TFSA"].avg_cost,
            (lines["NON_REGISTERED"].avg_cost + lines["TFSA"].avg_cost) / 2,
        }
        # Three pools, so pooling them says something none of them does.
        assert position.pooled_differs
        # The pool that sold out stays, last, with what it realized.
        assert position.lines[-1].holding.closed
        assert position.lines[-1].holding.realized == Decimal(300)
        assert position.first_traded == "2025-08-14"
        # The price it first went in at, as traded, with no split since.
        assert position.first_buy == FirstBuy(
            "2025-08-14",
            Decimal("150.25"),
            Currency.USD,
        )
        assert position.held_for(date(2026, 8, 13)) == (0, 11)


def test_by_account_lists_broker_accounts(temp_ctx: TempContext) -> None:
    with temp_ctx():
        _seed_two_types()

        position = symbol_position(_valuation(), "TESTTKR", by_account=True)

        assert position is not None
        assert [line.view.pool for line in position.lines] == [
            "WS-PERSONAL",
            "WS-TFSA",
            "QT-RRSP",
        ]


def test_a_symbol_the_folio_never_traded_has_no_position(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        _seed_two_types()

        assert symbol_position(_valuation(), "NEVERHELD") is None


def test_a_holding_the_valuation_currency_hides_has_no_position(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(FX)
        seed_transaction(ticker="CADCO.TO", currency="CAD", amount="-200", units="10")
        cached = build()
        assert cached.result is not None

        # A CAD holding has no USD figures to show.
        in_usd = FolioValuation.build(
            cached.frame,
            cached.result,
            currency=Currency.USD,
        )

        assert symbol_position(in_usd, "CADCO.TO") is None


def _ranged(price_range: PriceRange, anchor: int) -> RangeHistory:
    return RangeHistory(
        price_range,
        PricePoint("2025-10-01", Decimal(anchor)),
        (PricePoint("2026-04-01", Decimal(120)),),
    )


def test_each_move_runs_from_its_anchor_to_the_live_price() -> None:
    quote = Quote(
        symbol="AAA",
        ysymbol="AAA",
        price=Decimal(150),
        prev_close=Decimal(140),
        status=QuoteStatus.OK,
    )
    history = {PriceRange.YEAR_1: _ranged(PriceRange.YEAR_1, 100)}

    moves = price_moves(quote, history, intraday={})

    year = moves[PriceRange.YEAR_1]
    assert year is not None
    assert year.change == Decimal(50)
    assert year.change_pct == Decimal("0.5")
    assert year.points == (Decimal(100), Decimal(120), Decimal(150))
    assert year.multiple == Decimal("1.5")
    # The day is the previous close's, as `folio dash` shows it, even with no
    # intraday bars to draw its shape from.
    day = moves[PriceRange.DAY_1]
    assert day is not None
    assert day.change == Decimal(10)
    assert day.points == (Decimal(140), Decimal(150))
    # Nothing reaches back far enough for the rest.
    assert moves[PriceRange.YEARS_5] is None
    assert moves[PriceRange.HOURS_2] is None


def _bar(when: str, close: int) -> PricePoint:
    return PricePoint(when, Decimal(close))


def test_a_session_chart_runs_from_an_official_close_and_is_drawn_from_bars() -> None:
    quote = Quote(
        symbol="AAA",
        ysymbol="AAA",
        price=Decimal(150),
        prev_close=Decimal(140),
        status=QuoteStatus.OK,
    )
    # The last three daily closes, as the stored `2d` keeps them.
    stored = RangeHistory(
        PriceRange.DAYS_2,
        _bar("2026-09-28", 130),
        (_bar("2026-09-29", 135), _bar("2026-09-30", 145)),
    )
    live = {
        PriceRange.HOURS_2: RangeHistory(
            PriceRange.HOURS_2,
            _bar("2026-10-01T15:00:00+00:00", 148),
            (_bar("2026-10-01T17:00:00+00:00", 149),),
        ),
        PriceRange.DAY_1: RangeHistory(
            PriceRange.DAY_1,
            _bar("2026-09-30T19:55:00+00:00", 139),
            (
                _bar("2026-10-01T13:30:00+00:00", 141),
                _bar("2026-10-01T17:00:00+00:00", 149),
            ),
            fill=Decimal("0.5"),
        ),
        PriceRange.DAYS_2: RangeHistory(
            PriceRange.DAYS_2,
            _bar("2026-09-29T19:55:00+00:00", 144),
            (
                _bar("2026-09-30T13:30:00+00:00", 146),
                _bar("2026-10-01T17:00:00+00:00", 149),
            ),
            fill=Decimal("0.75"),
        ),
    }

    moves = price_moves(quote, {PriceRange.DAYS_2: stored}, live)

    day = moves[PriceRange.DAY_1]
    assert day is not None
    # Measured from the official previous close, never the last bar (139), and
    # drawn from the open with no bar for the close before it.
    assert day.anchor == Decimal(140)
    assert day.points == (Decimal(141), Decimal(149), Decimal(150))
    assert (day.start, day.fill) == ("2026-10-01", Decimal("0.5"))
    two_days = moves[PriceRange.DAYS_2]
    assert two_days is not None
    # The bars start Sep 30, so the close before them is Sep 29's.
    assert two_days.anchor == Decimal(135)
    assert (two_days.start, two_days.fill) == ("2026-09-30", Decimal("0.75"))
    # Two hours has no daily close behind it: its first bar is the start.
    hours = moves[PriceRange.HOURS_2]
    assert hours is not None
    assert (hours.anchor, hours.start) == (Decimal(148), "2026-10-01T15:00:00+00:00")


def test_an_unpriced_quote_has_no_moves() -> None:
    quote = Quote(symbol="AAA", ysymbol="AAA", status=QuoteStatus.NOT_FOUND)
    history = {PriceRange.YEAR_1: _ranged(PriceRange.YEAR_1, 100)}

    assert set(price_moves(quote, history, {}).values()) == {None}


def _row(symbol: str, week: str | None, market: int | None) -> PerformanceRow:
    """Build a matrix row with only a week's move and a market value."""
    moves = {}
    if week is not None:
        change = Decimal(week)
        moves[PriceRange.WEEK_1] = RangeMove(
            PriceRange.WEEK_1,
            anchor=Decimal(100),
            change=change,
            change_pct=change / 100,
            points=(),
            start="2026-09-24",
        )
    quote = Quote(symbol=symbol, ysymbol=symbol, status=QuoteStatus.OK)
    return PerformanceRow(
        symbol,
        quote,
        moves,
        market=None if market is None else Decimal(market),
    )


def test_the_matrix_sorts_best_first_with_the_unmeasured_last() -> None:
    rows = [_row("AAA", "2", 500), _row("BBB", None, 900), _row("CCC", "-3", 100)]

    def order(name: str, *, reverse: bool = False) -> list[str]:
        return [row.symbol for row in sort_performance(rows, name, reverse=reverse)]

    assert order("market") == ["BBB", "AAA", "CCC"]
    # A row with no move over the range trails, whichever way it is sorted.
    assert order("1WK") == ["AAA", "CCC", "BBB"]
    assert order("1wk", reverse=True) == ["CCC", "AAA", "BBB"]
    with pytest.raises(UnknownMatrixSortError, match="Try one of: market, 2h"):
        sort_performance(rows, "volume")


def test_the_matrix_reads_market_and_weights_from_the_pool(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        _seed_two_types()
        cached = build()
        assert cached.result is not None
        valuation = FolioValuation.build(
            cached.frame,
            cached.result,
            currency=Currency.CAD,
        )
        panel = valuation.panel(type_view("TFSA"))
        quote = Quote(symbol="TESTTKR", ysymbol="TESTTKR", status=QuoteStatus.OK)
        unheld = Quote(symbol="OTHER", ysymbol="OTHER", status=QuoteStatus.OK)

        held, other = performance_rows(
            panel,
            {"TESTTKR": quote, "OTHER": unheld},
            {},
        )

        # The TFSA's half of the holding: all of the pool, half the portfolio.
        assert held.weight_in_pool == Decimal(1)
        assert held.weight_in_folio == Decimal("0.5")
        assert held.market is not None
        # A security the pool does not hold has nothing to weigh.
        assert (other.market, other.weight_in_pool, other.weight_in_folio) == (
            None,
            None,
            None,
        )


def test_the_first_buy_is_the_first_one_with_a_price(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX)
        # A buy imported without its price, then one with it, then a split.
        seed_transaction(account="WS-TFSA", price=None, date="2025-08-14")
        seed_transaction(account="WS-TFSA", price="120", date="2025-08-15")
        seed_transaction(
            action="SPLIT",
            account="WS-TFSA",
            amount=None,
            price="1",
            units="2",
            date="2025-08-18",
        )
        # Arrived only by transfer, from an account the folio does not track.
        seed_transaction(
            action="TFR_IN",
            account="WS-TFSA",
            ticker="OTHER",
            amount=None,
            price=None,
            units="5",
        )
        valuation = _valuation()

        bought = symbol_position(valuation, "TESTTKR")
        moved_in = symbol_position(valuation, "OTHER")

        assert bought is not None
        assert bought.first_buy == FirstBuy(
            "2025-08-15",
            Decimal(120),
            Currency.USD,
            split_since=True,
        )
        assert moved_in is not None
        assert moved_in.first_buy is None
        # One pool each: pooled figures would only repeat it.
        assert not bought.pooled_differs
