"""Tests for the Yahoo Finance adapter, run against a stand-in yfinance."""

from __future__ import annotations

import socket
import sys
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pandas as pd
import pytest
from yfinance.exceptions import YFPricesMissingError, YFRateLimitError

from db import aliases
from models import QuoteSplit, TickerAlias
from services.price_history import PricePoint
from services.quote_service import QuoteService
from services.yahoo_provider import YahooProvider, load_yfinance

from .fixtures.yfinance_mocking import (
    FailingFastInfo,
    FakeTicker,
    FastInfo,
    stand_in_provider,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    from .helpers.context import TempContext


@pytest.fixture
def yahoo(monkeypatch: pytest.MonkeyPatch) -> Generator[YahooProvider]:
    """Put the real adapter behind the service, probing a local host instead of Yahoo.

    Yields:
        The adapter the service now fetches through.
    """
    with socket.create_server(("127.0.0.1", 0)) as server:
        provider = YahooProvider(host=server.getsockname()[:2])
        monkeypatch.setattr(QuoteService, "provider", provider)
        yield provider


# -- REACHING THE PROVIDER ---------------------------------------------------


def test_loading_the_provider_lets_a_failed_request_raise() -> None:
    # Hidden, yfinance reports a failed request as a symbol with no price.
    assert load_yfinance().config.debug.hide_exceptions is False


def test_reachability_is_asked_once() -> None:
    with socket.create_server(("127.0.0.1", 0)) as server:
        provider = YahooProvider(host=server.getsockname()[:2])
        assert provider.reachable()

    # The host is gone now, and the answer already given still stands.
    assert provider.reachable()


def test_the_probe_gives_up_once_its_budget_is_spent() -> None:
    # Every address shares one budget, so none is tried once it is gone.
    with socket.create_server(("127.0.0.1", 0)) as server:
        provider = YahooProvider(host=server.getsockname()[:2], probe_seconds=0)

        assert not provider.reachable()


def test_offline_never_imports_yfinance(
    temp_ctx: TempContext,
    yahoo: YahooProvider,  # noqa: ARG001 (puts the real adapter in place)
) -> None:
    with temp_ctx():
        sys.modules.pop("yfinance", None)

        result = QuoteService.get_quotes(["MSFT"], offline=True)

        assert result["MSFT"].price is None
        assert "yfinance" not in sys.modules, "offline reached for the provider"


# -- SPELLING ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("MSFT", "MSFT"),
        ("RY.TO", "RY.TO"),  # already a Yahoo exchange suffix
        ("BN.TO", "BN.TO"),
        ("VNTKR.V", "VNTKR.V"),  # TSX Venture, also a dotted exchange suffix
        ("REI-UN.TO", "REI-UN.TO"),
        ("BRK.B", "BRK-B"),  # a US share class Yahoo spells with a dash
        ("REI.UN.TO", "REI-UN.TO"),
        ("DLR.U.TO", "DLR-U.TO"),
        ("CAR.UN.TO", "CAR-UN.TO"),
    ],
)
def test_spelling(temp_ctx: TempContext, symbol: str, expected: str) -> None:
    with temp_ctx():
        assert YahooProvider().spell(symbol) == expected


def test_a_configured_override_beats_the_spelling_rule(temp_ctx: TempContext) -> None:
    # Matched whatever the case it was configured in.
    with temp_ctx(quotes={"symbol_overrides": {"vntkr.to": "VNTKR.V"}}):
        provider = YahooProvider()

        assert provider.spell("VNTKR.TO") == "VNTKR.V"
        # Everything else still follows the rule.
        assert provider.spell("REI.UN.TO") == "REI-UN.TO"


def test_the_symbol_override_reaches_the_provider(
    temp_ctx: TempContext,
    yahoo: YahooProvider,  # noqa: ARG001 (puts the real adapter in place)
) -> None:
    """A configured override must survive the whole config-to-fetch path."""
    tickers = {"VNTKR.V": FakeTicker(FastInfo(1.5, 1.4, "CAD"))}

    with (
        temp_ctx(quotes={"symbol_overrides": {"VNTKR.TO": "VNTKR.V"}}),
        stand_in_provider(tickers),
    ):
        QuoteService.refresh(["VNTKR.TO"])
        cached = QuoteService.cached(["VNTKR.TO"])["VNTKR.TO"]

    assert cached.provider_symbol == "VNTKR.V"
    assert cached.price == Decimal("1.5")


def test_a_ticker_reused_after_its_rename_is_quoted_under_its_own_symbol(
    temp_ctx: TempContext,
    yahoo: YahooProvider,  # noqa: ARG001 (puts the real adapter in place)
) -> None:
    # SPY became VOO, and a new security has since listed as SPY.
    renamed = TickerAlias(
        old_ticker="SPY",
        new_ticker="VOO",
        effective_date="2030-01-01",
    )
    tickers = {
        "SPY": FakeTicker(FastInfo(12.5, 12.0, "USD")),
        "VOO": FakeTicker(FastInfo(500.0, 495.0, "USD")),
    }

    with temp_ctx(), stand_in_provider(tickers):
        aliases.write([renamed])
        QuoteService.refresh(["SPY"])
        cached = QuoteService.cached(["SPY"])["SPY"]

    assert cached.provider_symbol == "SPY"
    assert cached.price == Decimal("12.5")


# -- PRICES --------------------------------------------------------------------


def test_prices_read_fast_info() -> None:
    tickers = {
        "AAA": FakeTicker(FastInfo(200.0, 190.0, "USD")),
        "BBB": FakeTicker(FastInfo(50.0, 55.0, "CAD")),
    }

    with stand_in_provider(tickers):
        fetched = YahooProvider().prices(["AAA", "BBB"]).prices

    assert fetched["AAA"].price == 200
    assert fetched["AAA"].prev_close == 190
    assert fetched["AAA"].currency == "USD"
    assert fetched["AAA"].quote_time
    assert fetched["BBB"].currency == "CAD"


def test_prices_sort_each_symbol_by_how_it_came_back() -> None:
    tickers = {
        "AAA": FakeTicker(FastInfo(200.0, 190.0, "USD")),
        # Known to the provider, but with no price.
        "GHOST": FakeTicker(FastInfo(None, 0.0, "USD")),
        "NOSUCH": FakeTicker(FailingFastInfo(YFPricesMissingError("NOSUCH", ""))),
        "SLOW": FakeTicker(FailingFastInfo(YFRateLimitError())),
        "DOWN": FakeTicker(FailingFastInfo(OSError("connection reset by peer"))),
    }

    with stand_in_provider(tickers):
        fetch = YahooProvider().prices([*tickers, "UNKNOWN"])

    # No price, no prices on record and not listed at all: all not found.
    assert set(fetch.prices) == {"AAA"}
    assert fetch.throttled == {"SLOW"}
    assert fetch.failed == {"DOWN"}


def test_the_previous_close_is_the_regular_session_one() -> None:
    class Fast:
        last_price = 220.0
        previous_close = 219.53  # counts extended hours
        regular_market_previous_close = 209.66

        @property
        def currency(self) -> str:
            # `fast_info` computes lazily, so reading an attribute can itself raise.
            message = "no data"
            raise RuntimeError(message)

    class NoDailyBars:
        last_price = 220.0
        previous_close = 219.53
        regular_market_previous_close = None

    tickers = {"AAA": FakeTicker(Fast()), "BBB": FakeTicker(NoDailyBars())}

    with stand_in_provider(tickers):
        fetched = YahooProvider().prices(["AAA", "BBB"]).prices

    assert fetched["AAA"].prev_close == Decimal("209.66")
    assert fetched["AAA"].currency is None
    # A symbol with no daily bars is still better priced than not at all.
    assert fetched["BBB"].prev_close == Decimal("219.53")


# -- DESCRIPTIONS --------------------------------------------------------------


def test_describing_reads_info() -> None:
    class Exploding:
        """A ticker whose `.info` scrape fails, as a throttled one does."""

        fast_info = FastInfo(1.0, 1.0, "USD")

        @property
        def info(self) -> dict:
            message = "scrape failed"
            raise RuntimeError(message)

    tickers: dict[str, Any] = {
        "AAA": FakeTicker(
            FastInfo(200.0, 190.0, "USD"),
            {"shortName": "Alpha Inc", "sector": "Technology", "marketCap": 1_000},
        ),
        "LONG": FakeTicker(FastInfo(1.0, 1.0, "USD"), {"longName": "Long Name Inc"}),
        "BOOM": Exploding(),
        # An empty `.info` is a miss, not a name of "".
        "EMPTY": FakeTicker(FastInfo(1.0, 1.0, "USD"), {}),
    }

    with stand_in_provider(tickers):
        described = YahooProvider().describe(list(tickers))

    assert described["AAA"]["name"] == "Alpha Inc"
    assert described["AAA"]["sector"] == "Technology"
    assert described["AAA"]["market_cap"] == 1_000
    assert described["LONG"]["name"] == "Long Name Inc"
    # A failed scrape costs a description, never a price.
    assert set(described) == {"AAA", "LONG"}


def test_fundamentals_are_cached_in_one_set_of_units(
    temp_ctx: TempContext,
    yahoo: YahooProvider,  # noqa: ARG001 (puts the real adapter in place)
) -> None:
    """The provider mixes percentages, ratios and epoch seconds; the cache does not."""
    stock = {
        "quoteType": "EQUITY",
        "trailingEps": 17.97,
        "dividendYield": 0.77,  # a percentage
        "exDividendDate": 1795046400,  # midnight UTC, 2026-11-19
        "earningsTimestamp": 1785355200,  # the last report
        "earningsTimestampStart": 1793217600,  # the next one: 4pm ET, 2026-10-28
    }
    fund = {
        "quoteType": "ETF",
        # A fund's figures arrive under different keys from a stock's.
        "epsTrailingTwelveMonths": 7.68,
        "beta3Year": 0.99,
        "trailingAnnualDividendRate": 0.0,
        "netExpenseRatio": 0.09,  # a percentage
        "netAssets": 35_000_000_000,
    }
    tickers = {
        "AAA": FakeTicker(FastInfo(100.0, 99.0, "USD"), stock),
        "FFF": FakeTicker(FastInfo(50.0, 49.0, "CAD"), fund),
    }

    with temp_ctx(), stand_in_provider(tickers):
        QuoteService.refresh(["AAA", "FFF"])
        cached = QuoteService.cached()

    stock_fundamentals = cached["AAA"].fundamentals
    assert stock_fundamentals.quote_type == "EQUITY"
    assert stock_fundamentals.eps == Decimal("17.97")
    assert stock_fundamentals.dividend_yield == Decimal("0.0077")
    assert stock_fundamentals.ex_dividend_date == "2026-11-19"
    assert stock_fundamentals.earnings_date == "2026-10-28"

    fund_fundamentals = cached["FFF"].fundamentals
    assert fund_fundamentals.eps == Decimal("7.68")
    assert fund_fundamentals.beta == Decimal("0.99")
    assert fund_fundamentals.expense_ratio == Decimal("0.0009")
    assert fund_fundamentals.total_assets == Decimal(35_000_000_000)
    # A fund paying nothing reports a zero rate, which is no rate at all.
    assert fund_fundamentals.dividend_rate is None


def test_split_history_is_cached_with_the_metadata(
    temp_ctx: TempContext,
    yahoo: YahooProvider,  # noqa: ARG001 (puts the real adapter in place)
) -> None:
    info = {"shortName": "Alpha"}
    fast = FastInfo(100.0, 99.0, "USD")

    class SplitsFail:
        """A ticker that describes itself but cannot give its splits."""

        fast_info = fast

        def __init__(self) -> None:
            self.info = info

        @property
        def splits(self) -> pd.Series:
            message = "throttled"
            raise RuntimeError(message)

    history = pd.Series(
        [4.0, 0.1],
        index=pd.DatetimeIndex(["2022-06-10", "2024-03-01"], tz="America/New_York"),
    )
    tickers: dict[str, Any] = {
        "SPLIT": FakeTicker(fast, info, history),
        "FLAT": FakeTicker(fast, info),
        "FAIL": SplitsFail(),
    }

    with temp_ctx(), stand_in_provider(tickers):
        QuoteService.refresh(["SPLIT", "FLAT", "FAIL"])
        cached = QuoteService.cached()

    assert cached["SPLIT"].splits == (
        QuoteSplit("2022-06-10", Decimal(4)),
        QuoteSplit("2024-03-01", Decimal("0.1")),
    )
    # Known to have none is not the same as not knowing.
    assert cached["FLAT"].splits == ()
    assert cached["FAIL"].splits is None
    assert cached["FAIL"].name == "Alpha"


# -- DOWNLOADS -----------------------------------------------------------------

DAYS = pd.DatetimeIndex(["2026-09-29", "2026-09-30"])


def test_daily_closes_come_from_one_batched_download() -> None:
    frame = pd.DataFrame(
        {("AAA", "Close"): [10.0, 11.0], ("BBB", "Close"): [20.0, None]},
        DAYS,
    )

    with stand_in_provider({}, frame) as stand_in:
        closes = YahooProvider().daily_closes(["AAA", "BBB", "MISSING"])

    assert stand_in.downloaded == [["AAA", "BBB", "MISSING"]]
    assert closes["AAA"] == [
        PricePoint("2026-09-29", Decimal(10)),
        PricePoint("2026-09-30", Decimal(11)),
    ]
    # A NaN close is dropped rather than read as a price.
    assert closes["BBB"] == [PricePoint("2026-09-29", Decimal(20))]
    # A batch can come back covering fewer symbols than were asked for.
    assert "MISSING" not in closes


def test_daily_closes_read_a_single_symbol_download() -> None:
    # A flat frame, with the fields alone, is read as the one symbol asked for.
    frame = pd.DataFrame({"Close": [11.0]}, DAYS[-1:])

    with stand_in_provider({}, frame):
        closes = YahooProvider().daily_closes(["AAA"])

    assert closes == {"AAA": [PricePoint("2026-09-30", Decimal(11))]}


@pytest.mark.parametrize("frame", [pd.DataFrame(), None])
def test_an_empty_download_has_no_closes(frame: pd.DataFrame | None) -> None:
    with stand_in_provider({}, frame):
        assert YahooProvider().daily_closes(["AAA"]) == {}


def test_history_reads_each_interval(temp_ctx: TempContext) -> None:
    days = pd.bdate_range("2021-01-04", "2026-09-30")
    frame = pd.DataFrame({"Close": [float(n) for n in range(1, len(days) + 1)]}, days)

    # The stand-in hands back the same closes for every interval asked for.
    with temp_ctx(), stand_in_provider({}, frame) as stand_in:
        bars = YahooProvider().history(["AAA"], today=date(2026, 9, 30))["AAA"]

    assert len(stand_in.downloaded) == 4
    assert bars.monthly[0] == PricePoint("2021-01-04", Decimal(1))
    assert bars.daily[-1].when == "2026-09-30"
    # Hourly bars draw a shape within a day, so they keep their time.
    assert bars.hourly[-1].when == "2026-09-30T00:00:00+00:00"


def test_intraday_stamps_each_bar_in_utc(temp_ctx: TempContext) -> None:
    bars = pd.date_range(
        "2026-09-29 09:30",
        "2026-09-30 15:55",
        freq="5min",
        tz="America/New_York",
    )
    frame = pd.DataFrame(
        {("AAA", "Close"): [1.0] * len(bars), ("BBB", "Close"): [2.0] * len(bars)},
        bars,
    )

    with temp_ctx(), stand_in_provider({}, frame):
        fetched = YahooProvider().intraday(["AAA", "BBB", "MISSING"])

    # A batch can come back covering fewer symbols than were asked for, or none.
    assert set(fetched) == {"AAA", "BBB"}
    assert fetched["AAA"].fives[-1].when == "2026-09-30T19:55:00+00:00"
    with temp_ctx(), stand_in_provider({}, pd.DataFrame()):
        assert YahooProvider().intraday(["AAA"]) == {}
