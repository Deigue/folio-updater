"""Tests for the market quotes cache and service, against a fake provider."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from db import get_connection, get_rows
from domain import Column, Currency, PriceRange, QuoteStatus, Table
from models import Fundamentals, Quote, QuoteSplit
from services.price_history import PricePoint
from services.quote_provider import HistoryBars, IntradayBars
from services.quote_service import (
    NOT_FOUND_TTL_DAYS,
    QuoteService,
    RefreshResult,
)

from .fixtures.fake_quote_provider import provider_price
from .helpers.seed import seed_transaction

if TYPE_CHECKING:
    from .fixtures.fake_quote_provider import FakeQuoteProvider
    from .helpers.context import TempContext


def _age_cached(symbol: str, *, minutes: int) -> None:
    """Backdate a cached quote past its TTL, so it is stale."""
    stale = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
    with get_connection() as conn:
        conn.execute(
            f'UPDATE "{Table.QUOTES}" SET "{Column.Quote.FETCHED_AT}" = ? '
            f'WHERE "{Column.Quote.SYMBOL}" = ?',
            (stale, symbol),
        )
        conn.commit()


def test_a_refresh_caches_what_it_fetched(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_transaction(ticker="TESTTKR")

        result = QuoteService.refresh(["TESTTKR"])

        assert result.fetched == 1
        assert result.not_found == 0
        cached = QuoteService.cached(["TESTTKR"])["TESTTKR"]
        assert cached.price == 200
        assert cached.prev_close == 190
        assert cached.currency is Currency.USD
        assert cached.status is QuoteStatus.OK
        assert cached.source == "fake"
        assert cached.priced


def test_a_symbol_spelled_in_two_cases_is_fetched_and_counted_once(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx():
        result = QuoteService.refresh(["testtkr", "TESTTKR"])

        assert result.fetched == 1
        assert quote_provider.asked("prices") == [["TESTTKR"]]
        assert list(QuoteService.cached()) == ["TESTTKR"]


def test_metadata_lands_alongside_the_price(temp_ctx: TempContext) -> None:
    with temp_ctx():
        QuoteService.refresh(["TESTTKR"])

        cached = QuoteService.cached(["TESTTKR"])["TESTTKR"]
        assert cached.name == "Test Ticker Inc"
        assert cached.sector == "Technology"
        assert cached.market_cap == 1_000_000_000
        assert cached.meta_fetched_at is not None


def test_a_fresh_quote_is_not_refetched(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx():
        QuoteService.get_quotes(["TESTTKR"])
        QuoteService.get_quotes(["TESTTKR"])

        assert len(quote_provider.asked("prices")) == 1, "a fresh quote was refetched"


def test_a_stale_quote_is_refetched(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx(quotes={"ttl_minutes": 15}):
        QuoteService.get_quotes(["TESTTKR"])
        _age_cached("TESTTKR", minutes=60)

        QuoteService.get_quotes(["TESTTKR"])
        assert len(quote_provider.asked("prices")) == 2


def test_looking_up_a_typo_leaves_nothing_in_the_cache(temp_ctx: TempContext) -> None:
    with temp_ctx():
        quotes = QuoteService.lookup(["nosuch", "TESTTKR"])

        assert quotes["NOSUCH"].status is QuoteStatus.NOT_FOUND
        assert set(QuoteService.cached()) == {"TESTTKR"}


def test_looking_up_keeps_a_not_found_row_cached_before(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx():
        # A holding the provider cannot price, already negative-cached.
        QuoteService.refresh(["NOSUCH"])

        QuoteService.lookup(["NOSUCH"], refresh=True)

        assert QuoteService.cached()["NOSUCH"].status is QuoteStatus.NOT_FOUND
        assert len(quote_provider.asked("prices")) == 2


def test_refresh_bypasses_a_still_fresh_ttl(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx():
        QuoteService.get_quotes(["TESTTKR"])
        QuoteService.get_quotes(["TESTTKR"], refresh=True)

        assert len(quote_provider.asked("prices")) == 2


def test_a_symbol_the_provider_does_not_know_is_negative_cached(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx():
        result = QuoteService.get_quotes(["NOSUCH"])

        assert result["NOSUCH"].status is QuoteStatus.NOT_FOUND
        assert result["NOSUCH"].price is None
        assert not result["NOSUCH"].priced

        # Asking again must not hammer the provider for a delisted symbol.
        QuoteService.get_quotes(["NOSUCH"])
        assert len(quote_provider.asked("prices")) == 1


def test_a_negative_cache_expires_eventually(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx():
        QuoteService.get_quotes(["NOSUCH"])
        _age_cached("NOSUCH", minutes=(NOT_FOUND_TTL_DAYS + 1) * 24 * 60)

        QuoteService.get_quotes(["NOSUCH"])
        assert len(quote_provider.asked("prices")) == 2


def test_prices_that_all_vanish_at_once_are_an_outage(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx():
        QuoteService.refresh(["TESTTKR"])

        # Requests that failed without raising: no price for anyone.
        quote_provider.prices_known = {}
        result = QuoteService.refresh(["TESTTKR"])

        assert result.failed == {"TESTTKR"}
        cached = QuoteService.cached(["TESTTKR"])["TESTTKR"]
        assert cached.status is QuoteStatus.OK, "an outage was negative-cached"
        assert cached.price == 200


def test_an_unreachable_provider_is_never_asked(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx():
        QuoteService.refresh(["TESTTKR"])
        quote_provider.calls.clear()
        quote_provider.up = False

        quotes = QuoteService.get_quotes(["TESTTKR"], refresh=True)
        assert QuoteService.history(["TESTTKR"]) == {}
        assert QuoteService.intraday(["TESTTKR"]) == {}

        assert not quote_provider.calls, "the provider was asked while unreachable"
        assert quotes["TESTTKR"].price == 200


def test_offline_serves_whatever_is_cached_and_asks_nothing(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx():
        QuoteService.refresh(["TESTTKR"])
        quote_provider.calls.clear()

        result = QuoteService.get_quotes(["TESTTKR", "OTHER"], offline=True)

        assert result["TESTTKR"].price == 200
        assert result["OTHER"].price is None
        assert not quote_provider.calls


def test_every_requested_symbol_comes_back(temp_ctx: TempContext) -> None:
    with temp_ctx():
        result = QuoteService.get_quotes(["TESTTKR", "NOSUCH"])

        # "unpriced" must be distinguishable from "not asked for".
        assert set(result) == {"TESTTKR", "NOSUCH"}


def test_clear_empties_the_cache(temp_ctx: TempContext) -> None:
    with temp_ctx():
        QuoteService.refresh(["TESTTKR", "OTHER"])

        assert QuoteService.clear() == 2
        assert QuoteService.cached() == {}


def test_clear_can_drop_one_symbol(temp_ctx: TempContext) -> None:
    with temp_ctx():
        QuoteService.refresh(["TESTTKR", "OTHER"])

        assert QuoteService.clear(["TESTTKR"]) == 1
        assert set(QuoteService.cached()) == {"OTHER"}


def test_a_refresh_writes_one_row_per_symbol(temp_ctx: TempContext) -> None:
    with temp_ctx():
        QuoteService.refresh(["TESTTKR"])
        QuoteService.refresh(["TESTTKR"])

        with get_connection() as conn:
            rows = get_rows(conn, Table.QUOTES)
        assert len(rows) == 1, "the cache appended instead of replacing"


def test_metadata_is_not_refetched_within_its_ttl(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx(quotes={"ttl_minutes": 15, "metadata_ttl_days": 30}):
        info = quote_provider.info
        quote_provider.info = {}
        QuoteService.refresh(["TESTTKR"])
        # Metadata is still missing, so the next pass asks for it again.
        quote_provider.info = info
        QuoteService.refresh(["TESTTKR"])
        QuoteService.refresh(["TESTTKR"])

        assert len(quote_provider.asked("describe")) == 2, "refetched inside its TTL"


def test_fundamentals_are_held_to_their_shorter_ttl_only_when_shown(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx(quotes={"metadata_ttl_days": 30, "fundamentals_ttl_hours": 24}):
        QuoteService.get_quotes(["TESTTKR"])
        day_and_a_half = (datetime.now(UTC) - timedelta(hours=36)).isoformat()
        with get_connection() as conn:
            conn.execute(
                f'UPDATE "{Table.QUOTES}" SET "{Column.Quote.META_FETCHED_AT}" = ?',
                (day_and_a_half,),
            )
            conn.commit()

        # A dashboard only shows the name and sector: a month is fine.
        QuoteService.get_quotes(["TESTTKR"])
        assert len(quote_provider.asked("describe")) == 1

        # Showing P/E and dividend dates, a day and a half is too old.
        QuoteService.get_quotes(["TESTTKR"], fundamentals=True)
        assert len(quote_provider.asked("describe")) == 2


def test_a_quote_in_an_unsupported_currency_is_not_a_crash(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx():
        quote_provider.prices_known = {"TESTTKR": provider_price(10, 9, "JPY")}
        quote_provider.info = {}

        QuoteService.refresh(["TESTTKR"])

        cached = QuoteService.cached(["TESTTKR"])["TESTTKR"]
        # An unconvertible currency is dropped rather than stored or raised on;
        # the holding simply reads as unpriced downstream.
        assert cached.currency is None
        assert cached.price == 10


def test_a_quote_with_no_fetch_is_stale_and_unpriced() -> None:
    quote = Quote(symbol="TESTTKR", provider_symbol="TESTTKR")

    assert quote.age is None
    assert QuoteService.is_stale(quote)
    assert not quote.priced
    assert quote.day_change is None


def test_a_batch_of_quotes_is_as_old_as_its_oldest(temp_ctx: TempContext) -> None:
    now = datetime.now(UTC)
    fresh = Quote(symbol="AAA", provider_symbol="AAA", fetched_at=now)
    old = replace(fresh, symbol="BBB", fetched_at=now - timedelta(hours=2))

    with temp_ctx(quotes={"ttl_minutes": 15}):
        assert QuoteService.quote_age({"AAA": fresh}) == (now, False)
        assert QuoteService.quote_age({"AAA": fresh, "BBB": old}) == (
            old.fetched_at,
            True,
        )
        # Never fetched: no age to give, and stale by definition.
        never = Quote(symbol="CCC", provider_symbol="CCC")
        assert QuoteService.quote_age({"CCC": never}) == (None, True)


def test_day_change_needs_both_sides() -> None:
    assert Quote(
        symbol="X",
        provider_symbol="X",
        price=Decimal(12),
        prev_close=Decimal(10),
    ).day_change == Decimal(2)
    assert Quote(symbol="X", provider_symbol="X", price=Decimal(12)).day_change is None
    assert (
        Quote(symbol="X", provider_symbol="X", price=Decimal(12)).day_change_pct is None
    )
    # 93 against a 52-week high of 100: seven percent below it.
    off_high = Quote(
        symbol="X",
        provider_symbol="X",
        price=Decimal(93),
        fundamentals=Fundamentals(high_52=Decimal(100)),
    )
    assert off_high.from_high_52 == Decimal("-0.07")
    assert (
        Quote(symbol="X", provider_symbol="X", price=Decimal(93)).from_high_52 is None
    )


def test_reading_an_empty_cache_is_not_an_error(temp_ctx: TempContext) -> None:
    with temp_ctx():
        assert QuoteService.cached() == {}
        assert QuoteService.clear() == 0


def test_asking_for_nothing_fetches_nothing(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx():
        assert QuoteService.get_quotes([]) == {}
        assert QuoteService.refresh([]) == RefreshResult()
        assert QuoteService.clear([]) == 0
        assert not quote_provider.calls


def test_a_failed_request_leaves_the_cached_quote_as_it_was(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    # OTHER still prices, so this is one failed symbol, not an outage.
    with temp_ctx():
        QuoteService.refresh(["TESTTKR"])
        before = QuoteService.cached(["TESTTKR"])["TESTTKR"]

        quote_provider.failed = {"TESTTKR"}
        result = QuoteService.refresh(["TESTTKR", "OTHER"])

        assert result == RefreshResult(fetched=1, failed=frozenset({"TESTTKR"}))
        assert QuoteService.cached(["TESTTKR"])["TESTTKR"] == before


def test_only_the_throttled_symbols_fall_back_to_daily_closes(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    quote_provider.throttled = {"TESTTKR"}
    quote_provider.closes = {
        "TESTTKR": [
            PricePoint("2030-01-02", Decimal("412.5")),
            PricePoint("2030-01-03", Decimal("418.25")),
        ],
    }

    with temp_ctx():
        result = QuoteService.refresh(["TESTTKR", "OTHER"])
        cached = QuoteService.cached(["TESTTKR", "OTHER"])

    assert quote_provider.asked("daily_closes") == [["TESTTKR"]]
    assert result == RefreshResult(fetched=2, used_fallback=True)
    assert cached["TESTTKR"].price == Decimal("418.25")
    assert cached["TESTTKR"].prev_close == Decimal("412.5")
    # OTHER keeps its intraday price rather than a daily close.
    assert cached["OTHER"].price == 50


def test_a_single_daily_close_has_no_previous_close(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    quote_provider.throttled = {"TESTTKR"}
    quote_provider.closes = {"TESTTKR": [PricePoint("2030-01-03", Decimal(11))]}

    with temp_ctx():
        QuoteService.refresh(["TESTTKR"])
        cached = QuoteService.cached(["TESTTKR"])["TESTTKR"]

    assert cached.price == 11
    assert cached.prev_close is None


def test_a_throttled_symbol_the_fallback_also_misses_has_failed(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    quote_provider.throttled = {"TESTTKR"}

    with temp_ctx():
        result = QuoteService.refresh(["TESTTKR", "OTHER"])
        cached = QuoteService.cached(["TESTTKR", "OTHER"])

    assert result == RefreshResult(
        fetched=1,
        failed=frozenset({"TESTTKR"}),
        used_fallback=True,
    )
    assert "TESTTKR" not in cached, "a failed symbol was cached"


def test_unknown_split_history_is_asked_for_inside_the_metadata_ttl(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx(quotes={"metadata_ttl_days": 30}):
        quote_provider.info = {"TESTTKR": {"name": "Test", "splits": None}}
        QuoteService.refresh(["TESTTKR"])
        quote_provider.info = {"TESTTKR": {"name": "Test", "splits": ()}}
        QuoteService.refresh(["TESTTKR"])
        QuoteService.refresh(["TESTTKR"])

        assert len(quote_provider.asked("describe")) == 2
        assert QuoteService.cached()["TESTTKR"].splits == ()


def test_a_failed_split_fetch_keeps_the_history_already_cached(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    split = QuoteSplit("2022-06-10", Decimal(4))

    # With no TTL to speak of, every refresh asks for the metadata again.
    with temp_ctx(quotes={"metadata_ttl_days": 0}):
        quote_provider.info = {"TESTTKR": {"name": "Test", "splits": (split,)}}
        QuoteService.refresh(["TESTTKR"])
        quote_provider.info = {"TESTTKR": {"name": "Test", "splits": None}}
        QuoteService.refresh(["TESTTKR"])

        assert QuoteService.cached()["TESTTKR"].splits == (split,)


def test_reading_the_cache_can_be_narrowed(temp_ctx: TempContext) -> None:
    with temp_ctx():
        QuoteService.refresh(["TESTTKR", "OTHER"])

        assert set(QuoteService.cached(["OTHER"])) == {"OTHER"}


def test_clearing_no_symbols_clears_nothing(temp_ctx: TempContext) -> None:
    with temp_ctx():
        QuoteService.refresh(["TESTTKR"])

        assert QuoteService.clear([]) == 0
        assert set(QuoteService.cached()) == {"TESTTKR"}


def test_a_zero_price_is_not_a_price() -> None:
    """Nothing trades at zero, so a zero is a failure in numeric clothing."""
    quote = Quote(
        symbol="X",
        provider_symbol="X",
        price=Decimal(0),
        fetched_at=datetime.now(UTC),
        status=QuoteStatus.OK,
    )

    assert not quote.priced


def _monthly(anchor: int) -> HistoryBars:
    """Bars reaching only the whole-listing range: a monthly close then and now."""
    return HistoryBars(
        daily=[],
        weekly=[],
        monthly=[
            PricePoint("2020-01-31", Decimal(anchor)),
            PricePoint("2026-09-30", Decimal(200)),
        ],
        hourly=[],
    )


def test_history_is_fetched_once_a_day_and_replaced_rather_than_grown(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx():
        quote_provider.bars = {"TESTTKR": _monthly(100)}
        first = QuoteService.history(["TESTTKR"])
        assert QuoteService.history(["TESTTKR"]) == first
        assert len(quote_provider.asked("history")) == 1, "refetched the same day"

        quote_provider.bars = {"TESTTKR": _monthly(120)}
        again = QuoteService.history(["TESTTKR"], refresh=True)

        assert again["TESTTKR"][PriceRange.ALL].anchor.close == Decimal(120)
        with get_connection() as conn:
            assert len(get_rows(conn, Table.QUOTE_HISTORY)) == 2
        assert QuoteService.history(["TESTTKR"], offline=True) == again

        # Clearing a symbol's quote takes its history with it.
        QuoteService.clear(["TESTTKR"])
        assert QuoteService.history(["TESTTKR"], offline=True) == {}
        assert QuoteService.history([]) == {}


def test_history_the_ranges_cannot_use_is_not_stored(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    quote_provider.bars = {"TESTTKR": HistoryBars([], [], [], [])}

    with temp_ctx():
        assert QuoteService.history(["TESTTKR"]) == {}


def test_intraday_ranges_are_fetched_live_and_never_offline(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    # The bar before the two hours anchors them.
    minutes = [
        PricePoint("2026-09-30T17:50:00+00:00", Decimal(1)),
        PricePoint("2026-09-30T19:55:00+00:00", Decimal(2)),
    ]
    quote_provider.live = {
        "TESTTKR": IntradayBars(minutes=minutes, fives=[]),
        "OTHER": IntradayBars(minutes=[], fives=[]),
    }

    with temp_ctx():
        live = QuoteService.intraday(["TESTTKR", "OTHER"])
        assert QuoteService.intraday(["TESTTKR"], offline=True) == {}

    # Bars that draw no range leave the symbol out.
    assert set(live) == {"TESTTKR"}
    assert live["TESTTKR"][PriceRange.HOURS_2].points[-1].close == 2
