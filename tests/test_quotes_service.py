"""Tests for the market quotes cache and service."""

from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import ModuleType
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pandas as pd
import pytest

from db import get_connection, get_rows, insert_or_replace_many
from domain import Column, Currency, QuoteStatus, Table
from services.quotes_service import (
    NOT_FOUND_TTL_DAYS,
    Quote,
    QuotesService,
    RefreshResult,
    _attr,
    _daily_closes,
    _is_rate_limited,
    _run_concurrently,
    _write,
)
from services.symbols import SymbolResolver

from .helpers.seed import seed_transaction

if TYPE_CHECKING:
    from collections.abc import Iterator
    from unittest.mock import MagicMock

    from .test_types import TempContext

RESOLVER = SymbolResolver([])


def _age_cached(symbol: str, *, minutes: int) -> None:
    """Backdate a cached quote so its TTL has expired."""
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

        result = QuotesService.refresh(["TESTTKR"], RESOLVER)

        assert result.fetched == 1
        assert result.not_found == 0
        cached = QuotesService.cached(["TESTTKR"])["TESTTKR"]
        assert cached.price == 200
        assert cached.prev_close == 190
        assert cached.currency is Currency.USD
        assert cached.status is QuoteStatus.OK
        assert cached.priced


def test_metadata_lands_alongside_the_price(temp_ctx: TempContext) -> None:
    with temp_ctx():
        QuotesService.refresh(["TESTTKR"], RESOLVER)

        cached = QuotesService.cached(["TESTTKR"])["TESTTKR"]
        assert cached.name == "Test Ticker Inc"
        assert cached.sector == "Technology"
        assert cached.market_cap == 1_000_000_000
        assert cached.meta_fetched_at is not None


def test_a_fresh_quote_is_not_refetched(
    temp_ctx: TempContext,
    quotes_fetch: MagicMock,
) -> None:
    with temp_ctx():
        QuotesService.get_quotes(["TESTTKR"], RESOLVER)
        assert quotes_fetch.call_count == 1

        QuotesService.get_quotes(["TESTTKR"], RESOLVER)
        assert quotes_fetch.call_count == 1, "a fresh quote was refetched"


def test_an_expired_quote_is_refetched(
    temp_ctx: TempContext,
    quotes_fetch: MagicMock,
) -> None:
    with temp_ctx(quotes={"ttl_minutes": 15}):
        QuotesService.get_quotes(["TESTTKR"], RESOLVER)
        _age_cached("TESTTKR", minutes=60)

        QuotesService.get_quotes(["TESTTKR"], RESOLVER)
        assert quotes_fetch.call_count == 2


def test_refresh_bypasses_a_still_fresh_ttl(
    temp_ctx: TempContext,
    quotes_fetch: MagicMock,
) -> None:
    with temp_ctx():
        QuotesService.get_quotes(["TESTTKR"], RESOLVER)
        QuotesService.get_quotes(["TESTTKR"], RESOLVER, refresh=True)

        assert quotes_fetch.call_count == 2


def test_a_symbol_the_provider_does_not_know_is_negative_cached(
    temp_ctx: TempContext,
    quotes_fetch: MagicMock,
) -> None:
    with temp_ctx():
        result = QuotesService.get_quotes(["NOSUCH"], RESOLVER)

        assert result["NOSUCH"].status is QuoteStatus.NOT_FOUND
        assert result["NOSUCH"].price is None
        assert not result["NOSUCH"].priced

        # Asking again must not hammer the provider for a delisted symbol.
        QuotesService.get_quotes(["NOSUCH"], RESOLVER)
        assert quotes_fetch.call_count == 1


def test_a_negative_cache_expires_eventually(
    temp_ctx: TempContext,
    quotes_fetch: MagicMock,
) -> None:
    with temp_ctx():
        QuotesService.get_quotes(["NOSUCH"], RESOLVER)
        _age_cached("NOSUCH", minutes=(NOT_FOUND_TTL_DAYS + 1) * 24 * 60)

        QuotesService.get_quotes(["NOSUCH"], RESOLVER)
        assert quotes_fetch.call_count == 2


def test_a_network_failure_keeps_the_cached_row(temp_ctx: TempContext) -> None:
    with temp_ctx():
        QuotesService.refresh(["TESTTKR"], RESOLVER)

        with patch.object(
            QuotesService,
            "_fetch_prices",
            side_effect=OSError("connection reset"),
        ):
            result = QuotesService.refresh(["TESTTKR"], RESOLVER)

        assert result.failed == 1
        assert result.fetched == 0
        # The price survives: a dashboard still renders, marked stale.
        assert QuotesService.cached(["TESTTKR"])["TESTTKR"].price == 200


def test_throttling_falls_back_to_daily_closes(temp_ctx: TempContext) -> None:
    with temp_ctx():
        daily = {
            "TESTTKR": {
                "price": 111.0,
                "prev_close": 110.0,
                "currency": "USD",
                "quote_time": datetime.now(UTC).isoformat(),
            },
        }
        with (
            patch.object(
                QuotesService,
                "_fetch_prices",
                side_effect=RuntimeError("Too Many Requests. Rate limited."),
            ),
            patch.object(QuotesService, "_fetch_daily", return_value=daily) as fallback,
        ):
            result = QuotesService.refresh(["TESTTKR"], RESOLVER)

        assert fallback.called
        assert result.used_fallback
        assert result.fetched == 1
        assert QuotesService.cached(["TESTTKR"])["TESTTKR"].price == 111


@pytest.mark.parametrize(
    ("message", "throttled"),
    [
        ("Too Many Requests", True),
        ("429 Client Error", True),
        ("YFRateLimitError: rate limit exceeded", True),
        ("connection reset by peer", False),
        ("No data found for this symbol", False),
    ],
)
def test_only_throttling_triggers_the_fallback(
    message: str,
    *,
    throttled: bool,
) -> None:
    assert _is_rate_limited(RuntimeError(message)) is throttled


def test_offline_never_imports_yfinance(temp_ctx: TempContext) -> None:
    with temp_ctx():
        sys.modules.pop("yfinance", None)

        result = QuotesService.get_quotes(["TESTTKR"], RESOLVER, offline=True)

        assert result["TESTTKR"].price is None
        assert "yfinance" not in sys.modules, "offline reached for the provider"


def test_offline_serves_whatever_is_cached(temp_ctx: TempContext) -> None:
    with temp_ctx():
        QuotesService.refresh(["TESTTKR"], RESOLVER)

        result = QuotesService.get_quotes(["TESTTKR"], RESOLVER, offline=True)

        assert result["TESTTKR"].price == 200


def test_every_requested_symbol_comes_back(temp_ctx: TempContext) -> None:
    with temp_ctx():
        result = QuotesService.get_quotes(["TESTTKR", "NOSUCH"], RESOLVER)

        # "unpriced" must be distinguishable from "not asked for".
        assert set(result) == {"TESTTKR", "NOSUCH"}


def test_clear_empties_the_cache(temp_ctx: TempContext) -> None:
    with temp_ctx():
        QuotesService.refresh(["TESTTKR", "OTHER"], RESOLVER)

        assert QuotesService.clear() == 2
        assert QuotesService.cached() == {}


def test_clear_can_drop_one_symbol(temp_ctx: TempContext) -> None:
    with temp_ctx():
        QuotesService.refresh(["TESTTKR", "OTHER"], RESOLVER)

        assert QuotesService.clear(["TESTTKR"]) == 1
        assert set(QuotesService.cached()) == {"OTHER"}


def test_a_refresh_writes_one_row_per_symbol(temp_ctx: TempContext) -> None:
    with temp_ctx():
        QuotesService.refresh(["TESTTKR"], RESOLVER)
        QuotesService.refresh(["TESTTKR"], RESOLVER)

        with get_connection() as conn:
            rows = get_rows(conn, Table.QUOTES)
        assert len(rows) == 1, "the cache appended instead of replacing"


def test_metadata_is_not_refetched_within_its_ttl(temp_ctx: TempContext) -> None:
    with temp_ctx(quotes={"ttl_minutes": 15, "metadata_ttl_days": 30}):
        with patch.object(
            QuotesService,
            "_fetch_metadata",
            return_value={},
        ) as meta:
            QuotesService.refresh(["TESTTKR"], RESOLVER)
            first = meta.call_count

        # Metadata is missing, so the next pass still asks for it.
        assert first == 1

        QuotesService.refresh(["TESTTKR"], RESOLVER)
        with patch.object(QuotesService, "_fetch_metadata", return_value={}) as meta:
            QuotesService.refresh(["TESTTKR"], RESOLVER)

        assert meta.call_count == 0, "metadata was refetched inside its TTL"


def test_a_quote_in_an_unsupported_currency_is_not_a_crash(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        priced: dict[str, dict[str, Any]] = {
            "TESTTKR": {
                "price": 10.0,
                "prev_close": 9.0,
                "currency": "JPY",
                "quote_time": datetime.now(UTC).isoformat(),
            },
        }
        with (
            patch.object(QuotesService, "_fetch_prices", return_value=priced),
            patch.object(QuotesService, "_fetch_metadata", return_value={}),
        ):
            QuotesService.refresh(["TESTTKR"], RESOLVER)

        cached = QuotesService.cached(["TESTTKR"])["TESTTKR"]
        # An unconvertible currency is dropped rather than stored or raised on;
        # the position simply reads as unpriced downstream.
        assert cached.currency is None
        assert cached.price == 10


def test_a_quote_with_no_fetch_is_stale_and_unpriced() -> None:
    quote = Quote(symbol="TESTTKR", ysymbol="TESTTKR")

    assert quote.age is None
    assert quote.is_stale
    assert not quote.priced
    assert quote.day_change is None


def test_day_change_needs_both_sides() -> None:
    assert Quote(
        symbol="X",
        ysymbol="X",
        price=Decimal(12),
        prev_close=Decimal(10),
    ).day_change == Decimal(2)
    assert Quote(symbol="X", ysymbol="X", price=Decimal(12)).day_change is None


def test_reading_an_empty_cache_is_not_an_error(temp_ctx: TempContext) -> None:
    with temp_ctx():
        assert QuotesService.cached() == {}
        assert QuotesService.clear() == 0


def test_asking_for_nothing_fetches_nothing(
    temp_ctx: TempContext,
    quotes_fetch: MagicMock,
) -> None:
    with temp_ctx():
        assert QuotesService.get_quotes([], RESOLVER) == {}
        assert QuotesService.refresh([], RESOLVER) == RefreshResult()
        assert QuotesService.clear([]) == 0
        assert not quotes_fetch.called


class _FastInfo:
    """Stands in for yfinance's lazily-computed `fast_info`."""

    def __init__(self, price: float | None, prev: float, currency: str) -> None:
        self.last_price = price
        self.previous_close = prev
        self.currency = currency


class _FakeTicker:
    """Stands in for one `yf.Ticker`."""

    def __init__(self, fast: _FastInfo, info: dict | None = None) -> None:
        self.fast_info = fast
        self.info = info if info is not None else {}


class _FakeTickers:
    """Stands in for `yf.Tickers`, which exposes a `.tickers` mapping."""

    def __init__(self, tickers: dict[str, _FakeTicker]) -> None:
        self.tickers = tickers


def _fake_yfinance(
    tickers: dict[str, Any],
    frame: pd.DataFrame | None = None,
) -> ModuleType:
    """Build a stand-in `yfinance` module the fetch seams can import.

    The seams import `yfinance` lazily by name, so putting a module here in
    `sys.modules` exercises the real adapter code: how attributes are read off
    `fast_info`, how a miss is recognised, how the raw dict is shaped. Only the
    provider itself is replaced, never our own logic.
    """
    module = ModuleType("yfinance")
    module.Tickers = lambda names: _FakeTickers(  # ty: ignore[unresolved-attribute]
        {name: tickers[name] for name in names.split() if name in tickers},
    )
    module.Ticker = lambda name: tickers[name]  # ty: ignore[unresolved-attribute]
    module.download = lambda *_args, **_kwargs: frame  # ty: ignore[unresolved-attribute]
    return module


@contextmanager
def _provider(
    tickers: dict[str, Any],
    frame: pd.DataFrame | None = None,
) -> Iterator[None]:
    """Install the stand-in provider for the duration of a block."""
    with patch.dict(sys.modules, {"yfinance": _fake_yfinance(tickers, frame)}):
        yield


@pytest.mark.no_mock_quotes
def test_fetching_prices_reads_fast_info() -> None:
    tickers = {
        "AAA": _FakeTicker(_FastInfo(200.0, 190.0, "USD")),
        "BBB": _FakeTicker(_FastInfo(50.0, 55.0, "CAD")),
    }

    with _provider(tickers):
        fetched = QuotesService._fetch_prices(["AAA", "BBB"])  # noqa: SLF001

    assert fetched["AAA"]["price"] == 200.0
    assert fetched["AAA"]["prev_close"] == 190.0
    assert fetched["AAA"]["currency"] == "USD"
    assert fetched["AAA"]["quote_time"]
    assert fetched["BBB"]["currency"] == "CAD"


@pytest.mark.no_mock_quotes
def test_fetching_prices_skips_a_symbol_with_no_price() -> None:
    tickers = {
        "AAA": _FakeTicker(_FastInfo(200.0, 190.0, "USD")),
        # A symbol the provider knows of but has no last price for.
        "GHOST": _FakeTicker(_FastInfo(None, 0.0, "USD")),
    }

    with _provider(tickers):
        fetched = QuotesService._fetch_prices(["AAA", "GHOST", "UNKNOWN"])  # noqa: SLF001

    # A missing price and an unknown symbol are both simply absent, which is
    # what `_merge` reads as NOT_FOUND.
    assert set(fetched) == {"AAA"}


@pytest.mark.no_mock_quotes
def test_fetching_metadata_reads_info() -> None:
    tickers = {
        "AAA": _FakeTicker(
            _FastInfo(200.0, 190.0, "USD"),
            {
                "shortName": "Alpha Inc",
                "sector": "Technology",
                "exchange": "NMS",
                "marketCap": 1_000,
                "currency": "USD",
            },
        ),
    }

    with _provider(tickers):
        described = QuotesService._fetch_metadata(["AAA"])  # noqa: SLF001

    assert described["AAA"]["name"] == "Alpha Inc"
    assert described["AAA"]["sector"] == "Technology"
    assert described["AAA"]["market_cap"] == 1_000


@pytest.mark.no_mock_quotes
def test_fetching_metadata_falls_back_to_the_long_name() -> None:
    tickers = {
        "AAA": _FakeTicker(_FastInfo(1.0, 1.0, "USD"), {"longName": "Alpha Inc"}),
    }

    with _provider(tickers):
        described = QuotesService._fetch_metadata(["AAA"])  # noqa: SLF001

    assert described["AAA"]["name"] == "Alpha Inc"


@pytest.mark.no_mock_quotes
def test_fetching_metadata_survives_a_symbol_that_raises() -> None:
    class Exploding:
        """A ticker whose `.info` scrape fails, as a throttled one does."""

        fast_info = _FastInfo(1.0, 1.0, "USD")

        @property
        def info(self) -> dict:
            message = "scrape failed"
            raise RuntimeError(message)

    tickers: dict[str, Any] = {
        "AAA": _FakeTicker(_FastInfo(1.0, 1.0, "USD"), {"shortName": "Alpha"}),
        "BOOM": Exploding(),
        # An empty `.info` is a miss, not a name of "".
        "EMPTY": _FakeTicker(_FastInfo(1.0, 1.0, "USD"), {}),
    }

    with _provider(tickers):
        described = QuotesService._fetch_metadata(["AAA", "BOOM", "EMPTY"])  # noqa: SLF001

    # A failed scrape costs a name, never a price.
    assert set(described) == {"AAA"}


@pytest.mark.no_mock_quotes
def test_the_daily_fallback_reads_a_batched_download(
    temp_ctx: TempContext,
) -> None:
    frame = pd.DataFrame(
        {("AAA", "Close"): [10.0, 11.0], ("BBB", "Close"): [20.0, 21.0]},
    )

    with temp_ctx(), _provider({}, frame):
        fetched = QuotesService._fetch_daily(["AAA", "BBB"])  # noqa: SLF001

    assert fetched["AAA"]["price"] == 11.0
    assert fetched["AAA"]["prev_close"] == 10.0
    # The daily path cannot know the currency, so it says so rather than guess.
    assert fetched["AAA"]["currency"] is None
    assert set(fetched) == {"AAA", "BBB"}


@pytest.mark.no_mock_quotes
def test_the_daily_fallback_skips_what_the_download_missed(
    temp_ctx: TempContext,
) -> None:
    # A batch can come back covering fewer symbols than were asked for.
    frame = pd.DataFrame({("AAA", "Close"): [10.0, 11.0]})

    with temp_ctx(), _provider({}, frame):
        fetched = QuotesService._fetch_daily(["AAA", "MISSING"])  # noqa: SLF001

    assert set(fetched) == {"AAA"}


@pytest.mark.no_mock_quotes
def test_the_daily_fallback_handles_a_single_close(temp_ctx: TempContext) -> None:
    # yfinance flattens the columns when only one symbol was requested.
    frame = pd.DataFrame({"Close": [11.0]})

    with temp_ctx(), _provider({}, frame):
        fetched = QuotesService._fetch_daily(["AAA"])  # noqa: SLF001

    assert fetched["AAA"]["price"] == 11.0
    assert fetched["AAA"]["prev_close"] is None


@pytest.mark.no_mock_quotes
def test_the_daily_fallback_on_an_empty_download(temp_ctx: TempContext) -> None:
    with temp_ctx(), _provider({}, pd.DataFrame()):
        assert QuotesService._fetch_daily(["AAA"]) == {}  # noqa: SLF001

    with temp_ctx(), _provider({}, None):
        assert QuotesService._fetch_daily(["AAA"]) == {}  # noqa: SLF001


def test_running_nothing_concurrently_returns_nothing() -> None:
    calls: list[str] = []

    def fetch(symbol: str) -> str:
        calls.append(symbol)  # pragma: no cover - never reached
        return symbol  # pragma: no cover

    assert _run_concurrently(fetch, []) == []
    assert calls == []


def test_running_concurrently_drops_the_misses() -> None:
    def fetch(symbol: str) -> str | None:
        return None if symbol == "GHOST" else symbol.lower()

    found = _run_concurrently(fetch, ["AAA", "GHOST", "BBB"])

    assert sorted(found) == ["aaa", "bbb"]


def test_writing_no_rows_touches_nothing(temp_ctx: TempContext) -> None:
    with temp_ctx():
        # Guards the table-creating write against being called with an empty
        # batch, which would otherwise create the table for nothing.
        _write([])

        assert QuotesService.cached() == {}


def test_a_batched_write_of_nothing_writes_nothing(temp_ctx: TempContext) -> None:
    with temp_ctx():
        QuotesService.refresh(["TESTTKR"], RESOLVER)

        with get_connection() as conn:
            assert insert_or_replace_many(conn, Table.QUOTES, []) == 0
        assert set(QuotesService.cached()) == {"TESTTKR"}


def test_a_batched_write_replaces_rather_than_appends(temp_ctx: TempContext) -> None:
    with temp_ctx():
        QuotesService.refresh(["TESTTKR"], RESOLVER)
        rows = [
            {
                str(Column.Quote.SYMBOL): "TESTTKR",
                str(Column.Quote.YSYMBOL): "TESTTKR",
                str(Column.Quote.PRICE): 999.0,
                str(Column.Quote.STATUS): str(QuoteStatus.OK),
            },
        ]

        with get_connection() as conn:
            assert insert_or_replace_many(conn, Table.QUOTES, rows) == 1

        cached = QuotesService.cached()
        assert len(cached) == 1
        assert cached["TESTTKR"].price == 999


def test_reading_the_cache_can_be_narrowed(temp_ctx: TempContext) -> None:
    with temp_ctx():
        QuotesService.refresh(["TESTTKR", "OTHER"], RESOLVER)

        assert set(QuotesService.cached(["OTHER"])) == {"OTHER"}


def test_clearing_no_symbols_clears_nothing(temp_ctx: TempContext) -> None:
    with temp_ctx():
        QuotesService.refresh(["TESTTKR"], RESOLVER)

        assert QuotesService.clear([]) == 0
        assert set(QuotesService.cached()) == {"TESTTKR"}


def test_a_zero_price_is_not_a_price() -> None:
    """Nothing trades at zero, so a zero is a failure in numeric clothing."""
    quote = Quote(
        symbol="X",
        ysymbol="X",
        price=Decimal(0),
        fetched_at=datetime.now(UTC),
        status=QuoteStatus.OK,
    )

    assert not quote.priced


def test_attr_survives_a_provider_that_raises() -> None:
    """`fast_info` computes lazily, so reading an attribute can itself raise."""

    class Exploding:
        @property
        def last_price(self) -> float:
            message = "no data"
            raise RuntimeError(message)

    assert _attr(Exploding(), "last_price") is None
    assert _attr(Exploding(), "absent") is None


def test_daily_closes_reads_a_batched_download() -> None:
    frame = pd.DataFrame(
        {("AAA", "Close"): [10.0, 11.0], ("BBB", "Close"): [20.0, None]},
    )

    assert _daily_closes(frame, "AAA", single=False) == [10.0, 11.0]
    # A NaN close is dropped rather than read as a price.
    assert _daily_closes(frame, "BBB", single=False) == [20.0]
    # A symbol the download did not cover simply has no closes.
    assert _daily_closes(frame, "MISSING", single=False) == []


def test_daily_closes_reads_a_single_symbol_download() -> None:
    frame = pd.DataFrame({"Close": [10.0, 11.0]})

    # yfinance flattens the columns when only one symbol was requested.
    assert _daily_closes(frame, "AAA", single=True) == [10.0, 11.0]
