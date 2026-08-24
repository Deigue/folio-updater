"""Tests for valuing replayed positions against market quotes."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from domain import Currency, QuoteStatus, Scope
from domain.numeric import ZERO, dec
from engine.cache import build
from engine.fx_rates import FxRates, load_fx_rates
from engine.positions import (
    FolioPositions,
    Holding,
    HoldingSet,
    UnknownSortError,
    _Context,
    _dividends_by_symbol,
    _filtered_out,
    _holding,
    base_currency,
    held_symbols,
    sort_holdings,
    summarize_closed,
)
from services.quotes_service import Quote

from .helpers.seed import seed_fx, seed_transaction

if TYPE_CHECKING:
    from .test_types import TempContext

# A single rate for every date the tests use, so a conversion is one
# multiplication and the expected values stay readable.
RATE = "1.25"
FX_DATES = {
    "2025-08-14": RATE,
    "2025-08-15": RATE,
    "2025-08-18": RATE,
    "2025-08-19": RATE,
    "2025-08-20": RATE,
}


def _quote(
    symbol: str,
    price: str,
    prev_close: str,
    currency: Currency = Currency.USD,
    *,
    status: QuoteStatus = QuoteStatus.OK,
) -> Quote:
    """Build a quote directly, so a test states the price it wants."""
    return Quote(
        symbol=symbol,
        ysymbol=symbol,
        price=dec(price),
        prev_close=dec(prev_close),
        currency=currency,
        name=f"{symbol} Inc",
        fetched_at=datetime.now(UTC),
        status=status,
    )


def _frame() -> pd.DataFrame:
    """Replay whatever has been seeded and hand back the master frame."""
    return build().frame


def test_market_value_and_unrealized_are_exact(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        # 10 units at $100 USD, so book value is 1,000 USD = 1,250 CAD.
        seed_transaction(ticker="AAA", amount="-1000", price="100", units="10")

        holdings = FolioPositions(
            _frame(),
            {"AAA": _quote("AAA", "120", "110")},
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency=Currency.CAD,
        )

        held = holdings.holdings[0]
        assert held.units == 10
        assert held.book_value == Decimal(1250)
        # 120 USD * 1.25 = 150 CAD per share.
        assert held.price == Decimal(150)
        assert held.market_value == Decimal(1500)
        assert held.unrealized == Decimal(250)
        assert held.unrealized_pct == Decimal("0.2")


def test_the_day_move_comes_off_the_previous_close(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="AAA", amount="-1000", price="100", units="10")

        holdings = FolioPositions(
            _frame(),
            {"AAA": _quote("AAA", "120", "110")},
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency=Currency.CAD,
        )

        held = holdings.holdings[0]
        # (120 - 110) * 1.25 = 12.50 CAD per share.
        assert held.change == Decimal("12.5")
        # change / price, against the *current* price, not the previous close.
        assert held.change_pct == Decimal("12.5") / Decimal(150)
        assert held.day_pnl == Decimal(125)


def test_day_pnl_pct_divides_by_the_pool_not_the_position(
    temp_ctx: TempContext,
) -> None:
    """The column that would be dead if it divided by the position's own value.

    A large holding drifting slightly must outrank a tiny one jumping, because
    the question is what moved *the pool*.
    """
    with temp_ctx():
        seed_fx(FX_DATES)
        # BIG: 100 units, priced 100 -> market 12,500 CAD, day move +1/share.
        seed_transaction(ticker="BIG", amount="-9000", price="90", units="100")
        # SMALL: 1 unit, priced 100 -> market 125 CAD, day move +10/share.
        seed_transaction(ticker="SMALL", amount="-90", price="90", units="1")

        holdings = FolioPositions(
            _frame(),
            {
                "BIG": _quote("BIG", "100", "99"),
                "SMALL": _quote("SMALL", "100", "90"),
            },
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency=Currency.CAD,
        )

        by_symbol = {held.symbol: held for held in holdings.holdings}
        big, small = by_symbol["BIG"], by_symbol["SMALL"]

        # SMALL moved 10x harder per share...
        assert small.change_pct is not None
        assert big.change_pct is not None
        assert small.change_pct > big.change_pct

        # ...but BIG moved the pool far more, which is what P&L% reports.
        assert big.day_pnl_pct is not None
        assert small.day_pnl_pct is not None
        assert big.day_pnl_pct > small.day_pnl_pct

        # They are different
        assert big.day_pnl_pct != big.change_pct
        assert small.day_pnl_pct != small.change_pct

        # Every position's share sums to the pool's own day move.
        total = big.day_pnl_pct + small.day_pnl_pct
        assert holdings.total_day_pnl is not None
        assert holdings.total_market is not None
        assert total == holdings.total_day_pnl / holdings.total_market


def test_day_pnl_pct_changes_with_the_scope_displayed(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(account="WS-TFSA", ticker="AAA", amount="-1000", units="10")
        seed_transaction(
            account="WS-PERSONAL",
            ticker="BBB",
            amount="-1000",
            units="10",
        )

        quotes = {
            "AAA": _quote("AAA", "120", "110"),
            "BBB": _quote("BBB", "120", "110"),
        }
        frame = _frame()
        fx = load_fx_rates()

        narrow = FolioPositions(frame, quotes, fx).holdings(
            scope=Scope.TYPE,
            pool="TFSA",
            currency=Currency.CAD,
        )
        wide = FolioPositions(frame, quotes, fx).holdings(
            scope=Scope.FOLIO,
            currency=Currency.CAD,
        )

        aaa_narrow = narrow.holdings[0]
        aaa_wide = next(h for h in wide.holdings if h.symbol == "AAA")

        # Alone in the TFSA it accounts for that pool's whole day move; across
        # the folio the same dollars move twice as much market value, so the
        # percentage halves. Both readings are correct for their own scope.
        assert aaa_narrow.day_pnl_pct == Decimal(125) / Decimal(1500)
        assert aaa_wide.day_pnl_pct == Decimal(125) / Decimal(3000)


def test_the_two_weights_differ_on_a_narrowed_scope(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(account="WS-TFSA", ticker="AAA", amount="-1000", units="10")
        seed_transaction(
            account="WS-PERSONAL",
            ticker="BBB",
            amount="-1000",
            units="10",
        )

        quotes = {
            "AAA": _quote("AAA", "100", "100"),
            "BBB": _quote("BBB", "100", "100"),
        }
        frame = _frame()
        fx = load_fx_rates()
        folio_total = FolioPositions(frame, quotes, fx).market_value(Currency.CAD)

        narrow = FolioPositions(frame, quotes, fx).holdings(
            scope=Scope.TYPE,
            pool="TFSA",
            currency=Currency.CAD,
            folio_market=folio_total,
        )

        held = narrow.holdings[0]
        # The whole TFSA, but only half of everything owned. That second number
        # is the one that reveals real concentration.
        assert held.weight_in_pool == Decimal(1)
        assert held.weight_in_folio == Decimal("0.5")


def test_the_two_weights_match_on_the_portfolio_wide_view(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(account="WS-TFSA", ticker="AAA", amount="-1000", units="10")
        seed_transaction(
            account="WS-PERSONAL",
            ticker="BBB",
            amount="-1000",
            units="10",
        )

        quotes = {
            "AAA": _quote("AAA", "100", "100"),
            "BBB": _quote("BBB", "100", "100"),
        }
        holdings = FolioPositions(_frame(), quotes, load_fx_rates()).holdings(
            scope=Scope.FOLIO,
            currency=Currency.CAD,
        )

        for held in holdings.holdings:
            assert held.weight_in_pool == held.weight_in_folio


def test_a_closed_position_is_not_a_holding(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="AAA", amount="-1000", price="100", units="10")
        seed_transaction(
            action="SELL",
            ticker="AAA",
            amount="1200",
            price="120",
            units="10",
            date="2025-08-18",
        )

        holdings = FolioPositions(
            _frame(),
            {"AAA": _quote("AAA", "120", "110")},
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency=Currency.CAD,
        )

        assert holdings.holdings == []


def test_a_closed_position_still_counts_toward_totals(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            ticker="AAA",
            currency=Currency.CAD.value,
            amount="-1000",
            price="100",
            units="10",
        )
        seed_transaction(
            action="DIVIDEND",
            ticker="AAA",
            currency=Currency.CAD.value,
            amount="40",
            price=None,
            units=None,
            date="2025-08-17",
        )
        # Sell everything: cost 1000, proceeds 1200, so 200 realized.
        seed_transaction(
            action="SELL",
            ticker="AAA",
            currency=Currency.CAD.value,
            amount="1200",
            price="120",
            units="10",
            date="2025-08-18",
        )

        holdings = FolioPositions(
            _frame(),
            {},
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency=Currency.CAD,
        )

        assert holdings.holdings == []
        assert [h.symbol for h in holdings.closed] == ["AAA"]
        closed = holdings.closed[0]
        assert closed.realized == Decimal(200)
        assert closed.dividends == Decimal(40)
        assert closed.units == 0
        assert closed.priced is False

        assert holdings.total_realized == Decimal(200)
        assert holdings.total_dividends == Decimal(40)
        assert holdings.total_pnl == Decimal(240)

        # A closed position is never priced, so total_pnl is None
        assert closed.total_pnl is None
        assert closed.closed_total == Decimal(240)

        assert len(holdings.by_currency) == 1
        group = holdings.by_currency[0]
        assert group.count == 0
        assert group.realized == Decimal(200)
        assert group.dividends == Decimal(40)
        # Nothing is still open, so there is no cost base to take a return
        # against, however much the group earned before it was liquidated.
        assert group.book == ZERO
        assert group.total_pnl == Decimal(240)
        assert group.total_pnl_pct is None


def _liquidated(symbol: str, realized: str, dividends: str) -> Holding:
    """Build a position sold out of, carrying only what it earned while held."""
    return Holding(
        symbol=symbol,
        name=None,
        pool="",
        currency=Currency.CAD,
        units=ZERO,
        avg_cost=ZERO,
        book_value=ZERO,
        realized=Decimal(realized),
        dividends=Decimal(dividends),
        closed=True,
    )


def test_closed_positions_aggregate_into_one_earned_figure() -> None:
    """The row a currency group shows in place of every liquidated ticker."""
    totals = summarize_closed(
        [_liquidated("AAA", "200", "40"), _liquidated("BBB", "-50", "10")],
    )

    assert totals.count == 2
    assert totals.realized == Decimal(150)
    assert totals.dividends == Decimal(50)
    assert totals.total == Decimal(200)


def test_aggregating_no_closed_positions_reports_zeros_not_a_crash() -> None:
    """The view asks before it knows whether the group has any."""
    totals = summarize_closed([])

    assert totals.count == 0
    assert totals.total == ZERO


def test_a_missing_quote_is_excluded_from_totals_not_counted_as_zero(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="AAA", amount="-1000", price="100", units="10")
        seed_transaction(ticker="GHOST", amount="-500", price="50", units="10")

        holdings = FolioPositions(
            _frame(),
            {"AAA": _quote("AAA", "100", "100")},
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency=Currency.CAD,
        )

        ghost = next(h for h in holdings.holdings if h.symbol == "GHOST")
        assert not ghost.priced
        assert ghost.market_value is None
        assert ghost.weight_in_pool is None
        # Its book value still counts; only the market figures are withheld.
        assert ghost.book_value == Decimal(625)

        assert holdings.unpriced == ("GHOST",)
        # 10 * 100 USD * 1.25 only. A zero for GHOST would give the same total,
        # so the weight is what proves it was excluded rather than zeroed.
        assert holdings.total_market == Decimal(1250)
        aaa = next(h for h in holdings.holdings if h.symbol == "AAA")
        assert aaa.weight_in_pool == Decimal(1)


def test_a_quote_in_an_unconvertible_currency_reads_as_unpriced(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="AAA", amount="-1000", price="100", units="10")

        euro = _quote("AAA", "120", "110", Currency.EUR)
        holdings = FolioPositions(
            _frame(),
            {"AAA": euro},
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency=Currency.CAD,
        )

        # The FX table holds USDCAD only, so this cannot be valued. It must
        # degrade, not raise.
        assert not holdings.holdings[0].priced
        assert holdings.unpriced == ("AAA",)


def test_a_not_found_quote_does_not_price_a_position(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="AAA", amount="-1000", price="100", units="10")

        missing = Quote(
            symbol="AAA",
            ysymbol="AAA",
            fetched_at=datetime.now(UTC),
            status=QuoteStatus.NOT_FOUND,
        )
        holdings = FolioPositions(
            _frame(),
            {"AAA": missing},
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency=Currency.CAD,
        )

        assert not holdings.holdings[0].priced


def test_usd_display_drops_cad_denominated_holdings(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="USDCO", currency="USD", amount="-1000", units="10")
        seed_transaction(ticker="CADCO", currency="CAD", amount="-1000", units="10")

        quotes = {
            "USDCO": _quote("USDCO", "100", "100"),
            "CADCO": _quote("CADCO", "100", "100", Currency.CAD),
        }
        holdings = FolioPositions(_frame(), quotes, load_fx_rates()).holdings(
            scope=Scope.FOLIO,
            currency=Currency.USD,
        )

        # The `_USD` columns are blank by design for a CAD holding, so there is
        # nothing honest to show for it.
        assert [held.symbol for held in holdings.holdings] == ["USDCO"]


def test_cad_display_keeps_both_denominations(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="USDCO", currency="USD", amount="-1000", units="10")
        seed_transaction(ticker="CADCO", currency="CAD", amount="-1000", units="10")

        quotes = {
            "USDCO": _quote("USDCO", "100", "100"),
            "CADCO": _quote("CADCO", "100", "100", Currency.CAD),
        }
        holdings = FolioPositions(_frame(), quotes, load_fx_rates()).holdings(
            scope=Scope.FOLIO,
            currency=Currency.CAD,
        )

        assert len(holdings.holdings) == 2


def test_market_value_uses_todays_rate_while_book_value_keeps_its_own(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        # The buy settles on the 19th, so 1.10 must still be in force then;
        # the 20th is the latest rate held and is what "today" resolves to.
        seed_fx(
            {
                "2025-08-14": "1.10",
                "2025-08-15": "1.10",
                "2025-08-18": "1.10",
                "2025-08-19": "1.10",
                "2025-08-20": "2.00",
            },
        )
        seed_transaction(ticker="AAA", amount="-1000", price="100", units="10")

        holdings = FolioPositions(
            _frame(),
            {"AAA": _quote("AAA", "100", "100")},
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency=Currency.CAD,
        )

        held = holdings.holdings[0]
        # Bought when USDCAD was 1.10, so the book value is frozen there.
        assert held.book_value == Decimal(1100)
        # Valued today at 2.00, not at the historical rate.
        assert held.market_value == Decimal(2000)
        assert holdings.fx_rate == Decimal("2.00")


def test_native_currency_leaves_each_holding_alone(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="USDCO", currency="USD", amount="-1000", units="10")

        holdings = FolioPositions(
            _frame(),
            {"USDCO": _quote("USDCO", "120", "110")},
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency="native",
        )

        held = holdings.holdings[0]
        assert held.currency is Currency.USD
        # No conversion at all: the quote's own 120 USD.
        assert held.price == Decimal(120)
        assert held.market_value == Decimal(1200)


def test_a_zero_price_does_not_divide_by_zero(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="AAA", amount="-1000", price="100", units="10")

        # `_number` reads a zero price as no price at all, which is the honest
        # reading: a security does not trade at zero.
        holdings = FolioPositions(
            _frame(),
            {"AAA": _quote("AAA", "0", "0")},
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency=Currency.CAD,
        )

        assert not holdings.holdings[0].priced


def test_an_all_unpriced_pool_reports_no_total(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="AAA", amount="-1000", price="100", units="10")

        holdings = FolioPositions(
            _frame(),
            {},
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency=Currency.CAD,
        )

        # None, not zero: nothing was valued, so no total exists.
        assert holdings.total_market is None
        assert holdings.total_unrealized is None
        assert holdings.total_book == Decimal(1250)

        # The ratios go missing with them rather than reading 0.00%, which
        # would claim a flat day and a break-even position on a pool nobody
        # could price. A known cost base is not enough to rescue either.
        assert holdings.total_day_pnl_pct is None
        assert holdings.total_unrealized_pct is None


def test_an_empty_fx_table_does_not_crash(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="CADCO", currency="CAD", amount="-1000", units="10")

        holdings = FolioPositions(
            _frame(),
            {"CADCO": _quote("CADCO", "120", "110", Currency.CAD)},
            FxRates((), ()),
        ).holdings(
            scope=Scope.FOLIO,
            currency=Currency.CAD,
        )

        # A CAD holding priced in CAD needs no rate at all.
        assert holdings.holdings[0].market_value == Decimal(1200)
        assert holdings.fx_rate is None


def test_held_symbols_lists_only_open_positions(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="OPEN", amount="-1000", price="100", units="10")
        seed_transaction(ticker="CLOSED", amount="-1000", price="100", units="10")
        seed_transaction(
            action="SELL",
            ticker="CLOSED",
            amount="1200",
            price="120",
            units="10",
            date="2025-08-18",
        )

        assert held_symbols(_frame()) == ["OPEN"]


def test_an_account_scope_sees_only_its_own_units(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(account="IBKR-TFSA", ticker="AAA", amount="-1000", units="10")
        seed_transaction(account="WS-TFSA", ticker="AAA", amount="-2000", units="20")

        frame = _frame()
        quotes = {"AAA": _quote("AAA", "100", "100")}
        fx = load_fx_rates()

        one = FolioPositions(frame, quotes, fx).holdings(
            scope=Scope.ACCOUNT,
            pool="IBKR-TFSA",
            currency=Currency.CAD,
        )
        pooled = FolioPositions(frame, quotes, fx).holdings(
            scope=Scope.TYPE,
            pool="TFSA",
            currency=Currency.CAD,
        )

        assert one.holdings[0].units == 10
        assert pooled.holdings[0].units == 30


def test_total_pnl_is_unrealized_plus_realized_plus_dividends(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="AAA", amount="-1000", price="100", units="10")
        seed_transaction(
            action="DIVIDEND",
            ticker="AAA",
            amount="40",
            price=None,
            units=None,
            date="2025-08-18",
        )

        holdings = FolioPositions(
            _frame(),
            {"AAA": _quote("AAA", "120", "110")},
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency="native",
        )

        held = holdings.holdings[0]
        assert held.unrealized == Decimal(200)  # 1,200 market - 1,000 book
        assert held.realized == ZERO  # nothing sold
        assert held.dividends == Decimal(40)
        assert held.total_pnl == Decimal(240)
        assert held.total_pnl_pct == Decimal("0.24")  # 240 / 1,000 book


def test_a_usd_dividend_is_converted_before_it_joins_the_grand_total(
    temp_ctx: TempContext,
) -> None:
    """The native view's grand total is CAD, dividends included.

    Every other base-currency figure is read off the frame's CAD columns, so a
    USD holding's dividends have to be as well. Summing the native USD figure
    into a CAD total silently understated it by the whole exchange rate.
    """
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            ticker="AAA",
            currency=Currency.CAD.value,
            amount="-1000",
            price="100",
            units="10",
        )
        seed_transaction(
            action="DIVIDEND",
            ticker="USDCO",
            currency="USD",
            amount="80",
            price=None,
            units=None,
            date="2025-08-18",
        )
        seed_transaction(
            ticker="USDCO",
            currency="USD",
            amount="-1000",
            price="100",
            units="10",
        )

        holdings = FolioPositions(
            _frame(),
            {
                "AAA": _quote("AAA", "100", "100", Currency.CAD),
                "USDCO": _quote("USDCO", "100", "100"),
            },
            load_fx_rates(),
        ).holdings(scope=Scope.FOLIO, currency="native")

        usd_group = next(
            group for group in holdings.by_currency if group.currency is Currency.USD
        )
        # The group row stays in its own currency, untouched.
        assert usd_group.dividends == Decimal(80)
        # The grand total converts at the rate the dividend was paid at.
        assert holdings.total_dividends == Decimal(80) * dec(RATE)
        assert holdings.total_pnl == holdings.total_dividends

        # And matches what asking for CAD outright reports.
        in_cad = FolioPositions(
            _frame(),
            {
                "AAA": _quote("AAA", "100", "100", Currency.CAD),
                "USDCO": _quote("USDCO", "100", "100"),
            },
            load_fx_rates(),
        ).holdings(scope=Scope.FOLIO, currency=Currency.CAD)
        assert in_cad.total_dividends == holdings.total_dividends


def test_a_currency_filter_that_hides_holdings_refuses_a_deposit_return(
    temp_ctx: TempContext,
) -> None:
    """`-c USD` on a mixed pool cannot be measured against whole-pool deposits."""
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            ticker="AAA",
            currency=Currency.CAD.value,
            amount="-1000",
            price="100",
            units="10",
        )
        seed_transaction(
            ticker="USDCO",
            currency="USD",
            amount="-1000",
            price="100",
            units="10",
        )

        holdings = FolioPositions(
            _frame(),
            {
                "AAA": _quote("AAA", "110", "110", Currency.CAD),
                "USDCO": _quote("USDCO", "110", "110"),
            },
            load_fx_rates(),
        ).holdings(scope=Scope.FOLIO, currency=Currency.USD)

        assert [h.symbol for h in holdings.holdings] == ["USDCO"]
        assert holdings.excluded == 1
        assert holdings.deposits_measurable is False
        assert holdings.return_on(Decimal(2000)) is None


def test_a_usd_only_pool_keeps_its_deposit_return_under_a_currency_filter(
    temp_ctx: TempContext,
) -> None:
    """Nothing is hidden, so `-c USD` measures exactly what the native view does.

    The ratio is taken on the CAD leg either way, so asking for USD moves the
    displayed figures without moving the one cell that answers "what did this
    pool return on the money put into it".
    """
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            ticker="USDCO",
            currency="USD",
            amount="-1000",
            price="100",
            units="10",
        )

        frame = _frame()
        quotes = {"USDCO": _quote("USDCO", "110", "110")}

        in_usd = FolioPositions(frame, quotes, load_fx_rates()).holdings(
            scope=Scope.FOLIO,
            currency=Currency.USD,
        )
        native = FolioPositions(frame, quotes, load_fx_rates()).holdings(
            scope=Scope.FOLIO,
            currency="native",
        )

        assert in_usd.excluded == 0
        assert in_usd.deposits_measurable is True
        # 100 USD earned, read off the frame's CAD columns at 1.25.
        assert in_usd.total_pnl == Decimal(100)
        assert in_usd.total_pnl_cad == Decimal(125)
        assert native.total_pnl_cad == in_usd.total_pnl_cad

        deposited = Decimal(1250)
        assert in_usd.return_on(deposited) == Decimal("0.1")
        assert in_usd.return_on(deposited) == native.return_on(deposited)


def test_a_closed_position_carries_its_cad_earnings_under_a_currency_filter(
    temp_ctx: TempContext,
) -> None:
    """A sold-out USD position still reports what it earned in CAD.

    It has no market price to look up, so its whole contribution is the
    realized gain and dividends the frame already holds in CAD.
    """
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            ticker="SOLD",
            currency="USD",
            amount="-1000",
            price="100",
            units="10",
        )
        # Out at 120: 1,000 cost against 1,200 proceeds, so 200 USD realized.
        seed_transaction(
            action="SELL",
            ticker="SOLD",
            currency="USD",
            amount="1200",
            price="120",
            units="10",
            date="2025-08-18",
        )

        holdings = FolioPositions(_frame(), {}, load_fx_rates()).holdings(
            scope=Scope.FOLIO,
            currency=Currency.USD,
        )

        closed = holdings.closed[0]
        assert closed.realized == Decimal(200)  # USD, as the row displays it
        assert closed.total_pnl_cad == Decimal(250)  # the same 200 at 1.25
        assert holdings.total_pnl == Decimal(200)
        assert holdings.total_pnl_cad == Decimal(250)
        assert holdings.deposits_measurable is True
        assert holdings.return_on(Decimal(1250)) == Decimal("0.2")


def test_a_holding_with_no_rate_to_convert_by_reports_no_cad_earnings(
    temp_ctx: TempContext,
) -> None:
    """A USD quote needs no rate to show in USD, but does to reach CAD.

    So `-c USD` can price the row while the return's CAD numerator stays
    genuinely unknown, rather than being invented at a rate nobody holds.
    """
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            ticker="USDCO",
            currency="USD",
            amount="-1000",
            price="100",
            units="10",
        )

        holdings = FolioPositions(
            _frame(),
            {"USDCO": _quote("USDCO", "120", "110")},
            FxRates((), ()),
        ).holdings(scope=Scope.FOLIO, currency=Currency.USD)

        held = holdings.holdings[0]
        assert held.market_value == Decimal(1200)
        assert held.total_pnl == Decimal(200)
        assert held.total_pnl_cad is None
        assert holdings.return_on(Decimal(1250)) is None


def test_realized_gains_reach_the_holding(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="AAA", amount="-1000", price="100", units="10")
        # Sell half at 150: cost 500, proceeds 750, so 250 realized.
        seed_transaction(
            action="SELL",
            ticker="AAA",
            amount="750",
            price="150",
            units="5",
            date="2025-08-18",
        )

        holdings = FolioPositions(
            _frame(),
            {"AAA": _quote("AAA", "150", "150")},
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency="native",
        )

        held = holdings.holdings[0]
        assert held.realized == Decimal(250)
        assert held.units == 5
        # 750 market - 500 remaining book, plus the 250 already banked.
        assert held.unrealized == Decimal(250)
        assert held.total_pnl == Decimal(500)


def test_an_unpriced_holding_reports_no_total(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="AAA", amount="-1000", price="100", units="10")
        seed_transaction(
            action="DIVIDEND",
            ticker="AAA",
            amount="40",
            price=None,
            units=None,
            date="2025-08-18",
        )

        holdings = FolioPositions(
            _frame(),
            {},
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency="native",
        )

        held = holdings.holdings[0]
        # Dividends are history and stay knowable without a price...
        assert held.dividends == Decimal(40)
        # ...but a third of the sum is unknown, so the sum is withheld rather
        # than quietly reported as though it were complete.
        assert held.total_pnl is None
        assert held.total_pnl_pct is None


def test_dividends_pool_to_the_scope(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        for account in ("WS-TFSA", "IBKR-TFSA"):
            seed_transaction(
                account=account,
                ticker="AAA",
                amount="-1000",
                price="100",
                units="10",
            )
            seed_transaction(
                action="DIVIDEND",
                account=account,
                ticker="AAA",
                amount="40",
                price=None,
                units=None,
                date="2025-08-18",
            )

        frame = _frame()
        quotes = {"AAA": _quote("AAA", "100", "100")}
        fx = load_fx_rates()

        one = FolioPositions(frame, quotes, fx).holdings(
            scope=Scope.ACCOUNT,
            pool="WS-TFSA",
            currency="native",
        )
        pooled = FolioPositions(frame, quotes, fx).holdings(
            scope=Scope.TYPE,
            pool="TFSA",
            currency="native",
        )

        assert one.holdings[0].dividends == Decimal(40)
        assert pooled.holdings[0].dividends == Decimal(80)


def _sortable() -> list[Holding]:
    """Three holdings whose measures deliberately disagree on order."""
    seed_fx(FX_DATES)
    seed_transaction(ticker="BIG", amount="-9000", price="90", units="100")
    seed_transaction(ticker="MID", amount="-2000", price="100", units="20")
    seed_transaction(ticker="AAA", amount="-100", price="50", units="2")
    return (
        FolioPositions(
            _frame(),
            {
                "BIG": _quote("BIG", "100", "99"),
                "MID": _quote("MID", "80", "80"),
                "AAA": _quote("AAA", "500", "400"),
            },
            load_fx_rates(),
        )
        .holdings(
            scope=Scope.FOLIO,
            currency="native",
        )
        .holdings
    )


def test_sorting_defaults_to_largest_first(temp_ctx: TempContext) -> None:
    with temp_ctx():
        ordered = sort_holdings(_sortable(), "market")

        assert [h.symbol for h in ordered] == ["BIG", "MID", "AAA"]


def test_sorting_can_be_reversed(temp_ctx: TempContext) -> None:
    with temp_ctx():
        ordered = sort_holdings(_sortable(), "market", reverse=True)

        assert [h.symbol for h in ordered] == ["AAA", "MID", "BIG"]


def test_text_sorts_a_to_z_rather_than_largest_first(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        holdings = _sortable()

        assert [h.symbol for h in sort_holdings(holdings, "symbol")] == [
            "AAA",
            "BIG",
            "MID",
        ]
        assert [h.symbol for h in sort_holdings(holdings, "symbol", reverse=True)] == [
            "MID",
            "BIG",
            "AAA",
        ]


def test_sorting_reads_the_value_not_the_rendered_cell(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        holdings = _sortable()

        # AAA moved 100 a share, far more than BIG's 1, so it leads on the
        # per-share measure and trails on the position-sized one.
        assert sort_holdings(holdings, "change")[0].symbol == "AAA"
        assert sort_holdings(holdings, "market")[0].symbol == "BIG"


def test_an_unpriced_holding_sinks_in_either_direction(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="PRICED", amount="-1000", units="10")
        seed_transaction(ticker="GHOST", amount="-500", units="10")

        holdings = (
            FolioPositions(
                _frame(),
                {"PRICED": _quote("PRICED", "100", "100")},
                load_fx_rates(),
            )
            .holdings(
                scope=Scope.FOLIO,
                currency="native",
            )
            .holdings
        )

        # A blank is not a zero, so it never leads an ascending sort.
        for reverse in (False, True):
            assert sort_holdings(holdings, "market", reverse=reverse)[-1].symbol == (
                "GHOST"
            )


def test_sorting_by_something_that_is_not_a_column_is_refused(
    temp_ctx: TempContext,
) -> None:
    with (
        temp_ctx(),
        pytest.raises(UnknownSortError, match="Cannot sort by 'nonsense'"),
    ):
        sort_holdings(_sortable(), "nonsense")


def test_holdings_takes_a_sort(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="BIG", amount="-9000", price="90", units="100")
        seed_transaction(ticker="AAA", amount="-100", price="50", units="2")

        holdings = FolioPositions(
            _frame(),
            {"BIG": _quote("BIG", "100", "99"), "AAA": _quote("AAA", "500", "400")},
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency="native",
            sort="symbol",
        )

        assert [h.symbol for h in holdings.holdings] == ["AAA", "BIG"]


def _mixed_folio() -> None:
    """One USD holding and one CAD holding, both priced at round numbers."""
    seed_fx(FX_DATES)
    # 10 units at 100 USD: book 1,000 USD, which is 1,250 CAD historically.
    seed_transaction(ticker="USDCO", currency="USD", amount="-1000", units="10")
    # 10 units at 100 CAD: book 1,000 CAD.
    seed_transaction(ticker="CADCO", currency="CAD", amount="-1000", units="10")


def _mixed_quotes() -> dict[str, Quote]:
    return {
        "USDCO": _quote("USDCO", "120", "110"),
        "CADCO": _quote("CADCO", "150", "150", Currency.CAD),
    }


def test_native_keeps_each_holding_in_its_own_currency(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        _mixed_folio()

        holdings = FolioPositions(_frame(), _mixed_quotes(), load_fx_rates()).holdings(
            scope=Scope.FOLIO,
            currency="native",
        )

        by_symbol = {h.symbol: h for h in holdings.holdings}
        usd, cad = by_symbol["USDCO"], by_symbol["CADCO"]

        # The USD holding reports the price its broker shows, unconverted.
        assert usd.currency is Currency.USD
        assert usd.price == Decimal(120)
        assert usd.avg_cost == Decimal(100)
        assert usd.book_value == Decimal(1000)
        assert usd.market_value == Decimal(1200)

        assert cad.currency is Currency.CAD
        assert cad.price == Decimal(150)
        assert cad.market_value == Decimal(1500)


def test_the_base_figures_are_cad_even_in_native_mode(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        _mixed_folio()

        holdings = FolioPositions(_frame(), _mixed_quotes(), load_fx_rates()).holdings(
            scope=Scope.FOLIO,
            currency="native",
        )

        usd = next(h for h in holdings.holdings if h.symbol == "USDCO")
        # Market value converts at today's rate: 1,200 USD * 1.25.
        assert usd.market_value_base == Decimal(1500)
        # Book value is read off the frame at its own historical rate, never
        # reconverted, because that is the figure CRA taxes.
        assert usd.book_value_base == Decimal(1250)
        assert usd.unrealized_base == Decimal(250)


def test_native_totals_are_grouped_by_currency(temp_ctx: TempContext) -> None:
    with temp_ctx():
        _mixed_folio()

        holdings = FolioPositions(_frame(), _mixed_quotes(), load_fx_rates()).holdings(
            scope=Scope.FOLIO,
            currency="native",
        )

        assert holdings.mixed_currency
        # CAD first: a Canadian folio reads home-currency-first.
        assert [g.currency for g in holdings.by_currency] == [
            Currency.CAD,
            Currency.USD,
        ]

        cad, usd = holdings.by_currency
        assert cad.count == 1
        assert cad.market == Decimal(1500)  # CAD
        assert usd.count == 1
        assert usd.market == Decimal(1200)  # USD, not converted

        # The grand total is the one converted figure: 1,500 CAD + 1,200 USD.
        assert holdings.base_currency is Currency.CAD
        assert holdings.total_market == Decimal(3000)


def test_weights_are_common_currency_even_when_rows_are_not(
    temp_ctx: TempContext,
) -> None:
    """A weight spans currencies by definition, so it cannot be native.

    Both holdings are worth 1,500 CAD, so both must read 50% however their own
    rows are denominated. Taking the ratio natively would give the USD holding
    1,200 / 2,700 instead.
    """
    with temp_ctx():
        _mixed_folio()

        holdings = FolioPositions(_frame(), _mixed_quotes(), load_fx_rates()).holdings(
            scope=Scope.FOLIO,
            currency="native",
        )

        for held in holdings.holdings:
            assert held.weight_in_pool == Decimal("0.5")


def _mixed_groups() -> HoldingSet:
    """Seed the two-currency folio and value it natively."""
    _mixed_folio()
    return FolioPositions(_frame(), _mixed_quotes(), load_fx_rates()).holdings(
        scope=Scope.FOLIO,
        currency="native",
    )


def test_a_groups_weights_are_summed_from_its_members(
    temp_ctx: TempContext,
) -> None:
    """Summed, not re-divided, so the two sides never change currency.

    Each holding's weight is already taken against the pool's CAD market
    value, so adding them is arithmetic. Dividing the USD group's own 1,200 by
    the pool's 3,000 CAD would silently compare two different currencies.
    """
    with temp_ctx():
        holdings = _mixed_groups()

        cad, usd = holdings.by_currency
        assert cad.weight_in_pool == Decimal("0.5")
        assert usd.weight_in_pool == Decimal("0.5")
        assert cad.weight_in_folio == Decimal("0.5")
        assert holdings.total_weight_in_folio == Decimal(1)


def test_a_groups_own_ratios_stay_in_its_own_currency(
    temp_ctx: TempContext,
) -> None:
    """Both sides of these are native, so the ratio is native too."""
    with temp_ctx():
        holdings = _mixed_groups()

        cad, usd = holdings.by_currency
        assert cad.unrealized_pct == Decimal("0.5")
        assert usd.unrealized_pct == Decimal("0.2")
        assert usd.total_pnl_pct == Decimal("0.2")


def test_the_pools_ratios_are_taken_in_the_base_currency(
    temp_ctx: TempContext,
) -> None:
    """Book 2,250 CAD, unrealized 750 CAD, and only the USD holding moved."""
    with temp_ctx():
        holdings = _mixed_groups()

        assert holdings.total_unrealized_pct == Decimal(750) / Decimal(2250)
        # 10 USD per unit over 10 units, at 1.25, against a 3,000 CAD pool.
        assert holdings.total_day_pnl_pct == Decimal(125) / Decimal(3000)


def test_the_pools_return_is_taken_on_the_money_put_in(
    temp_ctx: TempContext,
) -> None:
    """Not on book value, which no longer holds what closed positions cost."""
    with temp_ctx():
        holdings = _mixed_groups()

        assert holdings.total_pnl == Decimal(750)
        assert holdings.return_on(Decimal(7500)) == Decimal("0.1")
        # The book-based ratio the same pool would have shown, which is a
        # different question and a different number.
        assert holdings.total_unrealized_pct != Decimal("0.1")


@pytest.mark.parametrize("denominator", [None, ZERO])
def test_a_pool_with_nothing_put_in_reports_no_return(
    temp_ctx: TempContext,
    denominator: Decimal | None,
) -> None:
    """The return on nothing is not a number, however the nothing arrived."""
    with temp_ctx():
        assert _mixed_groups().return_on(denominator) is None


def test_a_single_currency_pool_is_not_mixed(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="CADCO", currency="CAD", amount="-1000", units="10")

        holdings = FolioPositions(
            _frame(),
            {"CADCO": _quote("CADCO", "150", "150", Currency.CAD)},
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency="native",
        )

        # Nothing was converted, so there is no second total to draw.
        assert not holdings.mixed_currency
        assert len(holdings.by_currency) == 1


def test_forcing_cad_leaves_one_group(temp_ctx: TempContext) -> None:
    with temp_ctx():
        _mixed_folio()

        holdings = FolioPositions(_frame(), _mixed_quotes(), load_fx_rates()).holdings(
            scope=Scope.FOLIO,
            currency=Currency.CAD,
        )

        assert not holdings.mixed_currency
        assert [g.currency for g in holdings.by_currency] == [Currency.CAD]
        assert holdings.total_market == Decimal(3000)


def test_forcing_usd_totals_in_usd(temp_ctx: TempContext) -> None:
    with temp_ctx():
        _mixed_folio()

        holdings = FolioPositions(_frame(), _mixed_quotes(), load_fx_rates()).holdings(
            scope=Scope.FOLIO,
            currency=Currency.USD,
        )

        # The CAD holding is dropped, so nothing is left to convert and the
        # total belongs in USD alongside the rows.
        assert base_currency(Currency.USD) is Currency.USD
        assert holdings.base_currency is Currency.USD
        assert holdings.total_market == Decimal(1200)


def test_a_usd_view_converts_a_cad_quote_back(temp_ctx: TempContext) -> None:
    """A US-listed holding whose quote comes back in CAD, shown in USD.

    Dual-listed securities do this. The conversion has to go CAD -> USD, which
    is the one path that divides by the rate rather than multiplying.
    """
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="AAA", currency="USD", amount="-1000", units="10")

        holdings = FolioPositions(
            _frame(),
            {"AAA": _quote("AAA", "125", "125", Currency.CAD)},
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency=Currency.USD,
        )

        # 125 CAD / 1.25 = 100 USD.
        assert holdings.holdings[0].price == Decimal(100)
        assert holdings.holdings[0].market_value == Decimal(1000)


def test_a_summary_row_without_a_symbol_is_not_a_holding(
    temp_ctx: TempContext,
) -> None:
    """A row with nothing to identify it is skipped."""
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="AAA", amount="-1000", units="10")

        context = _Context(
            quotes={},
            flags={},
            income={},
            scope=Scope.FOLIO,
            pool="",
            currency=Currency.CAD,
            fx=load_fx_rates(),
        )

        assert _holding({"Symbol": None}, context) is None
        assert _holding({"Symbol": ""}, context) is None
        assert _holding({}, context) is None

        # Nor is it a holding `-c USD` hid: it has no USD cost base, but then
        # it has no cost base at all.
        assert _filtered_out({"Symbol": None}, Scope.FOLIO) == 0
        assert _filtered_out({}, Scope.FOLIO) == 0


def test_an_empty_frame_holds_nothing() -> None:
    empty = pd.DataFrame()

    assert held_symbols(empty) == []
    holdings = FolioPositions(
        empty,
        {},
        FxRates((), ()),
    ).holdings(
        scope=Scope.FOLIO,
        currency=Currency.CAD,
    )
    assert holdings.holdings == []


def test_a_frame_with_no_tracked_symbols_holds_nothing(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        # Cash only: nothing carries a security.
        seed_transaction(
            action="CONTRIBUTION",
            ticker=None,
            amount="1000",
            price=None,
            units=None,
        )

        assert held_symbols(_frame()) == []


@pytest.mark.parametrize("scope", [Scope.ACCOUNT, Scope.TYPE, Scope.FOLIO])
def test_an_empty_folio_yields_an_empty_holding_set(
    temp_ctx: TempContext,
    scope: Scope,
) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(action="CONTRIBUTION", ticker=None, amount="1000", units=None)

        holdings = FolioPositions(
            _frame(),
            {},
            load_fx_rates(),
        ).holdings(
            scope=scope,
            pool="TESTACCT",
            currency=Currency.CAD,
        )

        assert holdings.holdings == []
        assert holdings.unpriced == ()


def _two_type_folio() -> None:
    """Seed a folio holding the same security in two account types."""
    seed_fx(FX_DATES)
    seed_transaction(account="WS-TFSA", ticker="AAA", amount="-1000", units="10")
    seed_transaction(account="WS-RRSP", ticker="AAA", amount="-2000", units="20")
    seed_transaction(account="WS-RRSP", ticker="BBB", amount="-500", units="5")
    seed_transaction(
        action="DIVIDEND",
        account="WS-TFSA",
        ticker="AAA",
        amount="40",
        price=None,
        units=None,
        date="2025-08-18",
    )


def test_positions_derives_each_pools_rollups_once(temp_ctx: TempContext) -> None:
    with temp_ctx():
        _two_type_folio()
        positions = FolioPositions(
            _frame(),
            {"AAA": _quote("AAA", "100", "100")},
            load_fx_rates(),
        )

        first = positions.rollups(Scope.FOLIO, None)
        positions.held_symbols()
        positions.market_value(Currency.CAD)
        positions.holdings(scope=Scope.FOLIO, currency=Currency.CAD)

        # Same object, not merely an equal one.
        assert positions.rollups(Scope.FOLIO, None) is first
        # A different pool is a different derivation.
        assert positions.rollups(Scope.TYPE, "TFSA") is not first


def test_pricing_later_keeps_the_rollups_already_derived(
    temp_ctx: TempContext,
) -> None:
    """Symbols have to be known before quotes can be fetched for them."""
    with temp_ctx():
        _two_type_folio()
        positions = FolioPositions(_frame(), {}, load_fx_rates())

        assert positions.held_symbols() == ["AAA", "BBB"]
        derived = positions.rollups(Scope.FOLIO, None)

        positions.price({"AAA": _quote("AAA", "100", "100")})

        assert positions.rollups(Scope.FOLIO, None) is derived
        # The total reflects the quotes supplied afterwards, not the empty set.
        assert positions.market_value(Currency.CAD) == Decimal(3750)


def test_priming_a_grain_matches_deriving_each_pool_alone(
    temp_ctx: TempContext,
) -> None:
    """The one-pass split has to agree with narrowing pool by pool."""
    with temp_ctx():
        _two_type_folio()
        frame = _frame()
        quotes = {"AAA": _quote("AAA", "100", "100")}
        fx = load_fx_rates()

        primed = FolioPositions(frame, quotes, fx)
        primed.prime(Scope.TYPE)

        for pool in ("TFSA", "RRSP"):
            alone = FolioPositions(frame, quotes, fx).holdings(
                scope=Scope.TYPE,
                pool=pool,
                currency=Currency.CAD,
            )
            assert (
                primed.holdings(
                    scope=Scope.TYPE,
                    pool=pool,
                    currency=Currency.CAD,
                )
                == alone
            )


def test_priming_leaves_a_pool_already_derived_alone(temp_ctx: TempContext) -> None:
    with temp_ctx():
        _two_type_folio()
        positions = FolioPositions(_frame(), {}, load_fx_rates())

        first = positions.rollups(Scope.TYPE, "TFSA")
        positions.prime(Scope.TYPE)

        assert positions.rollups(Scope.TYPE, "TFSA") is first


def test_priming_the_portfolio_grain_is_a_no_op(temp_ctx: TempContext) -> None:
    """Portfolio grain is one pool, so there is nothing to split."""
    with temp_ctx():
        _two_type_folio()
        positions = FolioPositions(_frame(), {}, load_fx_rates())

        positions.prime(Scope.FOLIO)

        assert positions.rollups(Scope.FOLIO, None).summary.empty is False


def test_a_security_that_paid_nothing_reads_as_zero_dividends(
    temp_ctx: TempContext,
) -> None:
    """The rollup skips non-income rows, so most securities are simply absent."""
    with temp_ctx():
        _two_type_folio()
        holdings = FolioPositions(
            _frame(),
            {"AAA": _quote("AAA", "100", "100"), "BBB": _quote("BBB", "100", "100")},
            load_fx_rates(),
        ).holdings(
            scope=Scope.FOLIO,
            currency=Currency.CAD,
        )

        by_symbol = {held.symbol: held for held in holdings.holdings}
        # 40 USD at 1.25.
        assert by_symbol["AAA"].dividends == Decimal(50)
        assert by_symbol["BBB"].dividends == ZERO


def test_a_dividend_row_carrying_no_symbol_is_skipped() -> None:
    """Income has to belong to a security before it can be totalled to one."""
    frame = pd.DataFrame(
        [
            {"Symbol": "AAA", "Dividend": 10.0, "Dividend_USD": None},
            {"Symbol": None, "Dividend": 99.0, "Dividend_USD": None},
        ],
    )

    assert _dividends_by_symbol(frame) == {"AAA": (Decimal(10), ZERO)}
