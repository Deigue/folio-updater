"""Market quotes from Yahoo Finance, cached in the folio database.

`import yfinance` is deliberately lazy, inside `load_yfinance`. It is a heavy
import, most commands never price anything, and `--offline` must be able to run
without the package resolving at all.
"""

from __future__ import annotations

import logging
import socket
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal
from functools import cache
from typing import TYPE_CHECKING, Any, Literal

from app import get_config
from db import quote_history
from db import quotes as quote_store
from domain import TORONTO_TZ, Currency, PriceRange, QuoteStatus
from domain.numeric import dec
from models import Fundamentals, Quote, QuoteHistory, QuoteSplit
from models.columns import Kind, column_of
from services.price_history import (
    PricePoint,
    RangeHistory,
    condense,
    condense_intraday,
    months_back,
)
from term import announce

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from types import ModuleType

    from services.symbols import SymbolResolver

logger = logging.getLogger(__name__)

SOURCE = "yfinance"
NOT_FOUND_TTL_DAYS = 7
# Fetches are network-bound, so cap the workers: a large folio must not open a
# hundred connections.
_MAX_FETCH_WORKERS = 8

# The host every price, history and crumb request goes through.
_PROVIDER_HOST = ("query1.finance.yahoo.com", 443)
# A connection opens in well under a second or not at all: an unreachable
# provider drops the handshake rather than refusing it.
_PROBE_TIMEOUT_SECONDS = 5


def load_yfinance() -> ModuleType:
    """Import yfinance, keeping its deprecation warnings off the terminal.

    Returns:
        The yfinance module.
    """
    import yfinance as yf  # noqa: PLC0415 (heavy, and unused when offline)

    warnings.filterwarnings("ignore", category=DeprecationWarning, module="^yfinance")
    # Hidden, a failed request looks exactly like a symbol Yahoo does not know.
    yf.config.debug.hide_exceptions = False
    return yf


@cache
def _provider_reachable() -> bool:
    """Whether Yahoo Finance accepts a connection, asked once per process.

    yfinance waits out a 30 second timeout on every request it cannot open, one
    symbol at a time behind its cookie lock, and reports each as a symbol with
    no price. Unreachable, a refresh would spend many minutes failing and then
    negative-cache every symbol, so one quick connection settles it first.

    Returns:
        False when the connection could not be opened, which is said once.
    """
    try:
        _connect_within(_PROVIDER_HOST, _PROBE_TIMEOUT_SECONDS)
    except OSError as error:
        announce.warning(
            f"Yahoo Finance is unreachable ({error}); using cached quotes and history.",
        )
        return False
    return True


def _connect_within(address: tuple[str, int], seconds: float) -> None:
    """Open and close one connection to a host, within a single time budget.

    `socket.create_connection` gives every address a host resolves to the full
    timeout, and Yahoo's resolves to half a dozen, so an unreachable provider
    would cost their sum. Here each address gets only what time is left.

    Args:
        address: The host and port.
        seconds: How long every attempt together may take.

    Raises:
        TimeoutError: If no address accepted a connection in time.
    """
    deadline = time.monotonic() + seconds
    for family, kind, proto, _, target in socket.getaddrinfo(
        *address,
        type=socket.SOCK_STREAM,
    ):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        with socket.socket(family, kind, proto) as sock:
            sock.settimeout(remaining)
            try:
                sock.connect(target)
            except OSError:
                continue
            return
    msg = f"no connection to {address[0]} within {seconds:g}s"
    raise TimeoutError(msg)


def _run_concurrently[T](
    fetch: Callable[[str], T | None],
    provider_symbols: Sequence[str],
) -> list[T]:
    """Run a per-symbol fetch across a thread pool, dropping empty results.

    Args:
        fetch: Looks up one provider symbol; returns None on a miss.
        provider_symbols: Provider spellings to fetch, never empty: `refresh` and
            `_collect_metadata` return before fetching nothing.

    Returns:
        Whatever `fetch` returned for each symbol, in no particular order,
        with the None misses already filtered out.
    """
    workers = min(len(provider_symbols), _MAX_FETCH_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(fetch, provider_symbols))
    return [result for result in results if result is not None]


@dataclass(frozen=True)
class RefreshResult:
    """What one refresh pass did.

    Attributes:
        fetched: Symbols priced successfully.
        failed: Symbols whose fetch failed, leaving any cached row in place.
        not_found: Symbols the provider does not know.
        used_fallback: Whether throttling forced the batched daily-close path,
            which yields the previous close rather than an intraday price.
    """

    fetched: int = 0
    failed: frozenset[str] = frozenset()
    not_found: int = 0
    used_fallback: bool = False


def _unique_upper(symbols: Iterable[str]) -> list[str]:
    """Upper-case symbols, keeping the first of any that then repeat."""
    return list(dict.fromkeys(symbol.upper() for symbol in symbols))


@dataclass(frozen=True)
class PriceFetch:
    """What one batch of price requests came back with.

    A provider symbol in none of these is one the provider does not know.

    Attributes:
        prices: Each priced provider symbol mapped to its raw price fields.
        throttled: Provider symbols that failed on a rate limit, which the
            daily-close download may still price.
        failed: Provider symbols that failed any other way.
    """

    prices: dict[str, dict] = field(default_factory=dict)
    throttled: frozenset[str] = frozenset()
    failed: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _PriceAttempt:
    """How pricing one provider symbol came out.

    Attributes:
        provider_symbol: The provider spelling asked for.
        raw: The raw price fields, when it priced.
        outcome: "throttled" or "failed" when the request failed, and None when
            it priced or the provider does not know the symbol.
    """

    provider_symbol: str
    raw: dict | None = None
    outcome: Literal["throttled", "failed"] | None = None


def _text(value: object) -> str | None:
    """Read a nullable text cell, treating pandas NaN sentinels as absent."""
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text.lower() in {"nan", "none", "<na>"} else text


def _number(value: object) -> Decimal | None:
    """Read a nullable numeric cell, keeping a genuine blank blank."""
    if _text(value) is None:
        return None
    number = dec(value)
    # `dec` maps anything unparseable to zero, and a zero price is not a price.
    return number or None


def _currency(value: object) -> Currency | None:
    """Read a currency cell, or None when the app does not support it."""
    text = _text(value)
    if text is None:
        return None
    try:
        return Currency(text.upper())
    except ValueError:
        # A quote in a currency we cannot recognize as yet.
        logger.debug("Quote carries unsupported currency '%s'", text)
        return None


class QuotesService:
    """Fetch, cache and read market quotes."""

    @staticmethod
    def cached(symbols: Iterable[str] | None = None) -> dict[str, Quote]:
        """Read cached quotes straight from the database.

        Args:
            symbols: Canonical symbols to read, or None for every cached row.

        Returns:
            Each symbol mapped to its cached `Quote`. Symbols with no row are
            simply absent.
        """
        wanted = None if symbols is None else [symbol.upper() for symbol in symbols]
        return quote_store.read(wanted)

    @classmethod
    def get_quotes(
        cls,
        symbols: Sequence[str],
        resolver: SymbolResolver,
        *,
        refresh: bool = False,
        offline: bool = False,
        fundamentals: bool = False,
    ) -> dict[str, Quote]:
        """Return a quote for every symbol, fetching only what is due.

        Args:
            symbols: Canonical folio symbols to price.
            resolver: Supplies the provider spelling for each symbol.
            refresh: Refetch every symbol regardless of its TTL.
            offline: Never touch the network. Returns whatever is cached,
                including nothing.
            fundamentals: Hold the metadata to the fundamentals TTL, which is
                hours rather than days, for a caller that shows them.

        Returns:
            Each requested symbol mapped to a `Quote`. A symbol that could not
            be priced is still present, carrying `price = None`, so a caller
            can tell "unpriced" apart from "not asked for".
        """
        wanted = _unique_upper(symbols)
        if not wanted:
            return {}

        cached = cls.cached(wanted)
        if offline:
            logger.debug("Offline: serving %d quote(s) from cache", len(cached))
            return _fill_gaps(wanted, cached, resolver)

        max_age = _meta_max_age(fundamentals=fundamentals)
        due = [
            symbol
            for symbol in wanted
            if refresh
            or _is_due(cached.get(symbol))
            or (fundamentals and cached[symbol].meta_older_than(max_age))
        ]
        if not due:
            return _fill_gaps(wanted, cached, resolver)

        result = cls.refresh(due, resolver, cached=cached, fundamentals=fundamentals)
        quotes = _fill_gaps(wanted, cls.cached(wanted), resolver)
        # A row left saying "not found" is an older answer: this time the fetch
        # failed, and the caller must not report the provider's verdict.
        return {
            symbol: replace(quote, status=QuoteStatus.ERROR)
            if symbol in result.failed and quote.status is QuoteStatus.NOT_FOUND
            else quote
            for symbol, quote in quotes.items()
        }

    @classmethod
    def refresh(
        cls,
        symbols: Sequence[str],
        resolver: SymbolResolver,
        cached: Mapping[str, Quote] | None = None,
        *,
        fundamentals: bool = False,
    ) -> RefreshResult:
        """Refetch the given symbols and write them to the cache.

        Args:
            symbols: Canonical folio symbols to refetch.
            resolver: Supplies the provider spelling for each symbol.
            cached: Already-read cached rows, to save a second query.
            fundamentals: Hold the metadata to the fundamentals TTL.

        Returns:
            A tally of what happened.
        """
        wanted = _unique_upper(symbols)
        if not wanted:
            return RefreshResult()
        if not _provider_reachable():
            return RefreshResult(failed=frozenset(wanted))

        known = dict(cached) if cached is not None else cls.cached(wanted)
        provider_symbols = {
            symbol: resolver.provider_symbol(symbol) for symbol in wanted
        }

        fetch = cls._fetch_prices(list(provider_symbols.values()))
        prices = dict(fetch.prices)
        failed = set(fetch.failed)
        if fetch.throttled:
            logger.info("Quote provider is throttling; falling back to daily closes")
            daily = cls._fetch_throttled(fetch.throttled)
            prices.update(daily)
            # Absent from the download, a symbol may still be throttled or not
            # be found at all, and only the second is worth caching.
            failed |= fetch.throttled - daily.keys()

        if not prices and any(known[s].priced for s in wanted if s in known):
            # A request can still fail without raising (an empty response, say).
            # Symbols that priced before do not all vanish at once, so this is
            # an outage, not a batch of delistings to negative-cache.
            logger.warning("No quote came back; using what is cached")
            return RefreshResult(failed=frozenset(wanted))

        metadata = cls._collect_metadata(
            wanted,
            provider_symbols,
            known,
            prices,
            _meta_max_age(fundamentals=fundamentals),
        )

        now = datetime.now(UTC)
        result = RefreshResult(used_fallback=bool(fetch.throttled))
        refreshed: list[Quote] = []
        for symbol in wanted:
            if provider_symbols[symbol] in failed:
                # Left as it was: a failed request says nothing about the symbol.
                result = replace(result, failed=result.failed | {symbol})
                continue
            quote, outcome = _merge(
                symbol,
                provider_symbols[symbol],
                known.get(symbol),
                prices.get(provider_symbols[symbol]),
                metadata.get(provider_symbols[symbol]),
                now,
            )
            refreshed.append(replace(quote, source=SOURCE))
            result = _tally(result, outcome)

        quote_store.write(refreshed)
        return result

    @classmethod
    def _collect_metadata(
        cls,
        symbols: Sequence[str],
        provider_symbols: Mapping[str, str],
        known: Mapping[str, Quote],
        prices: Mapping[str, dict],
        max_age: timedelta,
    ) -> dict[str, dict]:
        """Fetch metadata for the symbols that are stale under the slower TTL.

        `.info` is a full scrape per symbol and heavily rate-limited, so it runs
        once on first sight of a symbol and roughly monthly after, or daily for
        a caller showing fundamentals.
        """
        due = [
            provider_symbols[symbol]
            for symbol in symbols
            # Nothing to describe if the symbol did not price at all.
            if provider_symbols[symbol] in prices
            and (
                symbol not in known
                or known[symbol].meta_older_than(max_age)
                or known[symbol].name is None
                or known[symbol].splits is None
            )
        ]
        if not due:
            return {}

        try:
            return cls._fetch_metadata(due)
        except Exception as error:  # noqa: BLE001 (metadata is best-effort)
            logger.debug("Could not fetch quote metadata: %s", error)
            return {}

    @staticmethod
    def clear(symbols: Iterable[str] | None = None) -> int:
        """Drop cached quotes.

        Args:
            symbols: Canonical symbols to drop, or None to empty the cache.

        Returns:
            Number of rows removed.
        """
        wanted = None if symbols is None else [symbol.upper() for symbol in symbols]
        if wanted == []:
            return 0
        # A symbol's history goes with its quote, so both tables are cleared.
        quote_history.delete(wanted)
        return quote_store.delete(wanted)

    @classmethod
    def history(
        cls,
        symbols: Sequence[str],
        resolver: SymbolResolver,
        *,
        refresh: bool = False,
        offline: bool = False,
    ) -> dict[str, dict[PriceRange, RangeHistory]]:
        """Return each symbol's condensed price history, fetching what is due.

        History is fetched at most once a day: each range is measured from its
        anchor to the latest price, so the moves stay current between fetches.

        Args:
            symbols: Canonical folio symbols.
            resolver: Supplies the provider spelling for each symbol.
            refresh: Refetch every symbol, however recently it was fetched.
            offline: Never touch the network; return whatever is stored.

        Returns:
            Each symbol with any history mapped to its ranges. A symbol the
            provider has no history for is absent.
        """
        wanted = _unique_upper(symbols)
        if not wanted:
            return {}
        stored = _read_history(wanted)
        if not offline:
            today = datetime.now(TORONTO_TZ).date()
            due = [
                symbol
                for symbol in wanted
                if refresh
                or symbol not in stored
                or stored[symbol][0].astimezone(TORONTO_TZ).date() != today
            ]
            if due:
                stored.update(cls._refetch_history(due, resolver, today))
        return {symbol: stored[symbol][1] for symbol in wanted if symbol in stored}

    @classmethod
    def intraday(
        cls,
        symbols: Sequence[str],
        resolver: SymbolResolver,
        *,
        offline: bool = False,
    ) -> dict[str, dict[PriceRange, RangeHistory]]:
        """Return the ranges drawn from live bars (`2h`, `1d`, `2d`, `1wk`).

        These are never stored.

        Args:
            symbols: Canonical folio symbols.
            resolver: Supplies the provider spelling for each symbol.
            offline: Never touch the network, which leaves nothing to return.

        Returns:
            Each symbol the provider had bars for, mapped to its ranges.
        """
        wanted = _unique_upper(symbols)
        if offline or not wanted or not _provider_reachable():
            return {}
        provider_symbols = {
            resolver.provider_symbol(symbol): symbol for symbol in wanted
        }
        try:
            fetched = cls._fetch_intraday(list(provider_symbols))
        except Exception as error:  # noqa: BLE001 (the provider raises freely)
            logger.debug("Could not fetch intraday bars: %s", error)
            return {}
        return {
            provider_symbols[provider_symbol]: ranges
            for provider_symbol, ranges in fetched.items()
            if provider_symbol in provider_symbols
        }

    @classmethod
    def _refetch_history(
        cls,
        symbols: Sequence[str],
        resolver: SymbolResolver,
        today: date,
    ) -> dict[str, tuple[datetime, dict[PriceRange, RangeHistory]]]:
        """Fetch, condense and store the history of every symbol due.

        The fetch returns only symbols it found history for, so one the
        provider returned nothing for keeps whatever it had.
        """
        if not _provider_reachable():
            return {}
        provider_symbols = {
            resolver.provider_symbol(symbol): symbol for symbol in symbols
        }
        try:
            fetched = cls._fetch_history(list(provider_symbols), today=today)
        except Exception as error:  # noqa: BLE001 (the provider raises freely)
            logger.warning(
                "Could not fetch price history (%s); using what is kept",
                error,
            )
            return {}

        now = datetime.now(UTC)
        written: dict[str, tuple[datetime, dict[PriceRange, RangeHistory]]] = {}
        for provider_symbol, ranges in fetched.items():
            symbol = provider_symbols[provider_symbol]
            _write_history(symbol, ranges, now)
            written[symbol] = (now, ranges)
        return written

    # -- NETWORK ---------------------------------------------------------

    @classmethod
    def _fetch_prices(cls, provider_symbols: Sequence[str]) -> PriceFetch:
        """Fetch the price and previous close for a batch of symbols.

        Args:
            provider_symbols: Provider spellings to price.

        Returns:
            The prices, and which symbols were throttled or failed.
        """
        yf = load_yfinance()
        provider_errors = yf.exceptions
        tickers = yf.Tickers(" ".join(provider_symbols))
        now = datetime.now(UTC).isoformat()

        def _price(provider_symbol: str) -> _PriceAttempt:
            ticker = tickers.tickers.get(provider_symbol)
            if ticker is None:
                return _PriceAttempt(provider_symbol)
            fast = ticker.fast_info
            try:
                price = fast.last_price
            except provider_errors.YFTickerMissingError:
                # Also raised for an HTTP error body, which yfinance cannot tell
                # apart from a symbol with no prices.
                return _PriceAttempt(provider_symbol)
            except Exception as error:  # noqa: BLE001 (any request can fail)
                logger.debug("No price for %s: %r", provider_symbol, error)
                throttled = isinstance(error, provider_errors.YFRateLimitError)
                return _PriceAttempt(
                    provider_symbol,
                    outcome="throttled" if throttled else "failed",
                )
            if price is None:
                return _PriceAttempt(provider_symbol)
            return _PriceAttempt(
                provider_symbol,
                {
                    "price": price,
                    "prev_close": _previous_close(fast),
                    "currency": _attr(fast, "currency"),
                    "quote_time": now,
                },
            )

        prices: dict[str, dict] = {}
        throttled: set[str] = set()
        failed: set[str] = set()
        for attempt in _run_concurrently(_price, provider_symbols):
            if attempt.raw is not None:
                prices[attempt.provider_symbol] = attempt.raw
            elif attempt.outcome == "throttled":
                throttled.add(attempt.provider_symbol)
            elif attempt.outcome == "failed":
                failed.add(attempt.provider_symbol)
        return PriceFetch(prices, frozenset(throttled), frozenset(failed))

    @classmethod
    def _fetch_throttled(cls, provider_symbols: Iterable[str]) -> dict[str, dict]:
        """Price throttled symbols from their daily close, or none of them.

        Args:
            provider_symbols: Provider spellings a rate limit refused.

        Returns:
            Each provider symbol the download priced, mapped to its raw fields.
        """
        try:
            return cls._fetch_daily(sorted(provider_symbols))
        except Exception as error:  # noqa: BLE001 (the whole download can fail)
            logger.warning("Quote fallback failed (%s); using what is cached", error)
            return {}

    @classmethod
    def _fetch_daily(cls, provider_symbols: Sequence[str]) -> dict[str, dict]:
        """Fetch yesterday's close for a batch, in one request.

        The throttling fallback. One download covers every symbol, at the cost
        of a daily close instead of an intraday price.

        Args:
            provider_symbols: Provider spellings to price.

        Returns:
            Each provider symbol mapped to its raw price fields.
        """
        frame = load_yfinance().download(
            list(provider_symbols),
            period="5d",
            interval="1d",
            group_by="ticker",
            progress=False,
            auto_adjust=False,
            timeout=get_config().quotes_timeout_seconds,
        )
        if frame is None or frame.empty:
            return {}

        fetched: dict[str, dict] = {}
        for provider_symbol in provider_symbols:
            closes = _daily_closes(frame, provider_symbol)
            if not closes:
                continue
            fetched[provider_symbol] = {
                "price": closes[-1],
                "prev_close": closes[-2] if len(closes) > 1 else None,
                "currency": None,
                "quote_time": datetime.now(UTC).isoformat(),
            }
        return fetched

    @classmethod
    def _fetch_metadata(cls, provider_symbols: Sequence[str]) -> dict[str, dict]:
        """Fetch name, sector, exchange, market cap and fundamentals for a batch.

        Args:
            provider_symbols: Provider spellings to describe.

        Returns:
            Each provider symbol mapped to its metadata fields, the
            fundamentals keyed by their `Fundamentals` field names.
        """
        yf = load_yfinance()

        def _describe(provider_symbol: str) -> tuple[str, dict] | None:
            try:
                ticker = yf.Ticker(provider_symbol)
                info = ticker.info
            except Exception as error:  # noqa: BLE001 (per-symbol scrape)
                logger.debug("No metadata for %s: %s", provider_symbol, error)
                return None
            if not info:
                return None
            return provider_symbol, {
                "name": info.get("shortName") or info.get("longName"),
                "sector": info.get("sector"),
                "exchange": info.get("exchange") or info.get("fullExchangeName"),
                "market_cap": info.get("marketCap"),
                "currency": info.get("currency"),
                "quote_type": info.get("quoteType"),
                "trailing_pe": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "eps": _first(info, "trailingEps", "epsTrailingTwelveMonths"),
                "beta": _first(info, "beta", "beta3Year"),
                "dividend_rate": _first(
                    info,
                    "dividendRate",
                    "trailingAnnualDividendRate",
                ),
                "dividend_yield": _from_percent(info.get("dividendYield")),
                "last_dividend": info.get("lastDividendValue"),
                "ex_dividend_date": _epoch_date(info.get("exDividendDate")),
                # An earnings time is a moment in the trading day, so it is read
                # on the market's clock: after the close is still the same day.
                "earnings_date": _epoch_date(
                    _first(info, "earningsTimestampStart", "earningsTimestamp"),
                    TORONTO_TZ,
                ),
                "high_52": info.get("fiftyTwoWeekHigh"),
                "low_52": info.get("fiftyTwoWeekLow"),
                "avg_50": info.get("fiftyDayAverage"),
                "avg_200": info.get("twoHundredDayAverage"),
                "volume": _first(info, "regularMarketVolume", "volume"),
                "avg_volume": info.get("averageVolume"),
                "expense_ratio": _from_percent(info.get("netExpenseRatio")),
                "total_assets": _first(info, "totalAssets", "netAssets"),
                "category": info.get("category"),
                "fund_family": info.get("fundFamily"),
                "splits": _split_history(ticker),
            }

        return dict(_run_concurrently(_describe, provider_symbols))

    @classmethod
    def _fetch_history(
        cls,
        provider_symbols: Sequence[str],
        *,
        today: date,
    ) -> dict[str, dict[PriceRange, RangeHistory]]:
        """Fetch and condense a batch's history, in four downloads.

        Daily closes for the last year, weekly for five, monthly for the whole
        listing, and hourly bars for the last month's shape: enough for every
        stored range, and small whatever a symbol's age. The raw frames are
        condensed here and never kept.

        Args:
            provider_symbols: Provider spellings to fetch.
            today: The day the ranges are measured back from.

        Returns:
            Each provider symbol mapped to its condensed ranges.
        """
        # A margin either side of the earliest anchor, for holidays and gaps.
        margin = timedelta(days=14)
        daily_from = min(months_back(today, 12), date(today.year - 1, 12, 31))
        frames = {
            "daily": _download(
                provider_symbols,
                interval="1d",
                start=(daily_from - margin).isoformat(),
            ),
            "weekly": _download(
                provider_symbols,
                interval="1wk",
                start=(months_back(today, 60) - margin).isoformat(),
            ),
            "monthly": _download(provider_symbols, interval="1mo", period="max"),
            "hourly": _download(
                provider_symbols,
                interval="60m",
                start=(months_back(today, 1) - margin).isoformat(),
            ),
        }

        condensed: dict[str, dict[PriceRange, RangeHistory]] = {}
        for provider_symbol in provider_symbols:
            ranges = condense(
                _dated_closes(frames["daily"], provider_symbol),
                _dated_closes(frames["weekly"], provider_symbol),
                _dated_closes(frames["monthly"], provider_symbol),
                today=today,
                hourly=_dated_closes(frames["hourly"], provider_symbol, intraday=True),
            )
            if ranges:
                condensed[provider_symbol] = ranges
        return condensed

    @classmethod
    def _fetch_intraday(
        cls,
        provider_symbols: Sequence[str],
    ) -> dict[str, dict[PriceRange, RangeHistory]]:
        """Fetch and condense a batch's bars over the last week of sessions.

        One-minute bars for `2h`, five-minute bars for the rest. Regular
        sessions only: no pre- or after-market trading.

        Args:
            provider_symbols: Provider spellings to fetch.

        Returns:
            Each provider symbol mapped to its `2h`, `1d`, `2d` and `1wk` ranges.
        """
        minutes = _download(provider_symbols, interval="1m", period="5d")
        fives = _download(provider_symbols, interval="5m", period="5d")
        week_from = datetime.now(TORONTO_TZ).date() - timedelta(days=7)
        condensed: dict[str, dict[PriceRange, RangeHistory]] = {}
        for provider_symbol in provider_symbols:
            ranges = condense_intraday(
                _dated_closes(minutes, provider_symbol, intraday=True),
                _dated_closes(fives, provider_symbol, intraday=True),
                week_from=week_from,
            )
            if ranges:
                condensed[provider_symbol] = ranges
        return condensed


def _download(provider_symbols: Sequence[str], **window: str) -> Any:  # noqa: ANN401 (pandas)
    """Download a batch's bars over one window, grouped by symbol.

    Args:
        provider_symbols: Provider spellings to fetch.
        **window: The `interval`, and a `start` or a `period`.

    Returns:
        The provider's frame, or None when it returned nothing.
    """
    return load_yfinance().download(
        list(provider_symbols),
        group_by="ticker",
        progress=False,
        auto_adjust=False,
        timeout=get_config().quotes_timeout_seconds,
        **window,
    )


def _meta_max_age(*, fundamentals: bool) -> timedelta:
    """How old cached metadata may be: hours when fundamentals are shown."""
    config = get_config()
    if fundamentals:
        return timedelta(hours=config.quotes_fundamentals_ttl_hours)
    return timedelta(days=config.quotes_metadata_ttl_days)


def _split_history(ticker: Any) -> tuple[QuoteSplit, ...] | None:  # noqa: ANN401
    """Read a ticker's split history, or None when it could not be fetched."""
    try:
        history = ticker.splits
    except Exception as error:  # noqa: BLE001 (a separate provider request)
        logger.debug("No split history: %s", error)
        return None
    return tuple(
        QuoteSplit(when.strftime("%Y-%m-%d"), Decimal(str(ratio)))
        for when, ratio in history.items()
    )


def _first(info: Mapping[str, Any], *keys: str) -> object | None:
    """Read the first of several provider keys that holds anything."""
    for key in keys:
        value = info.get(key)
        if value is not None:
            return value
    return None


def _from_percent(value: object) -> Decimal | None:
    """Turn a provider percentage (0.77 meaning 0.77%) into a ratio."""
    if _text(value) is None:
        return None
    number = dec(value)
    return number / 100 if number else None


def _epoch_date(value: object, zone: tzinfo = UTC) -> str | None:
    """Turn a provider epoch timestamp, in seconds, into a `YYYY-MM-DD` date.

    Args:
        value: The timestamp. A bare date, such as an ex-dividend date, arrives
            as midnight UTC, which is why UTC is the default.
        zone: The clock to read the date on.

    Returns:
        The date, or None when the value is not a timestamp.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return None
    try:
        return datetime.fromtimestamp(float(value), zone).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _previous_close(fast: object) -> object | None:
    """Read the previous **regular session** close off a `fast_info`.

    `previous_close` groups a week of *pre/post-market* hourly bars by calendar
    date and takes the last bar of the day before (counting extended-hours).

    `regular_market_previous_close` takes the second-last row of the daily bar
    series, which is the prior session's official close and what every quote
    page measures the day's change from.

    Args:
        fast: A yfinance `fast_info` object.

    Returns:
        The prior regular-session close, falling back to the extended-hours
        figure.
    """
    regular = _attr(fast, "regular_market_previous_close")
    if regular is not None:
        return regular
    return _attr(fast, "previous_close")


def _attr(source: object, name: str) -> object | None:
    """Read one attribute off a provider object without trusting it exists."""
    try:
        return getattr(source, name, None)
    except Exception:  # noqa: BLE001 (fast_info computes lazily and can raise)
        return None


def _dated_closes(
    frame: Any,  # noqa: ANN401 (a pandas frame from the provider)
    provider_symbol: str,
    *,
    intraday: bool = False,
) -> list[PricePoint]:
    """Pull one symbol's closes, with their dates, out of a batched download.

    Args:
        frame: The provider's download, or None when it returned nothing.
        provider_symbol: The provider symbol to read.
        intraday: Stamp each close with its UTC time rather than its date.

    Returns:
        The closes, oldest first.
    """
    if frame is None or frame.empty:
        return []
    try:
        closes = _close_column(frame, provider_symbol).dropna()
    except (KeyError, TypeError, IndexError):
        return []
    points: list[PricePoint] = []
    for stamp, close in zip(closes.index, closes.to_numpy(), strict=True):
        moment = stamp.to_pydatetime()
        if intraday:
            aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
            when = aware.astimezone(UTC).isoformat()
        else:
            when = moment.date().isoformat()
        points.append(PricePoint(when, dec(close)))
    return points


def _close_column(frame: Any, provider_symbol: str) -> Any:  # noqa: ANN401 (pandas)
    """Find one symbol's closes in a download, whatever shape it came in.

    Grouped by ticker, a download keys its columns by symbol and then field,
    and yfinance keeps that shape even for a single symbol. A flat frame, with
    the fields alone, can only hold one symbol, so it is read as that one.
    """
    if frame.columns.nlevels > 1:
        return frame[provider_symbol]["Close"]
    return frame["Close"]


def _daily_closes(frame: Any, provider_symbol: str) -> list[float]:  # noqa: ANN401
    """Pull the close series for one symbol out of a batched download."""
    try:
        column = _close_column(frame, provider_symbol)
    except (KeyError, TypeError, IndexError):
        return []
    return [float(value) for value in column.dropna().tolist()]


def is_stale(quote: Quote) -> bool:
    """Whether a quote's price is older than the configured TTL.

    Args:
        quote: The cached quote.

    Returns:
        True when it was never fetched, or was fetched longer ago than
        `quotes_ttl_minutes`.
    """
    age = quote.age
    if age is None:
        return True
    return age > timedelta(minutes=get_config().quotes_ttl_minutes)


def _is_due(quote: Quote | None) -> bool:
    """Whether a cached quote should be refetched now."""
    if quote is None:
        return True
    if quote.status is QuoteStatus.NOT_FOUND:
        # Negative-cached: re-ask occasionally, not every run.
        age = quote.age
        return age is None or age > timedelta(days=NOT_FOUND_TTL_DAYS)
    return is_stale(quote)


def _merge(  # noqa: PLR0913, PLR0917
    symbol: str,
    provider_symbol: str,
    known: Quote | None,
    price: dict | None,
    meta: dict | None,
    now: datetime,
) -> tuple[Quote, QuoteStatus]:
    """Fold a fetch result over whatever was already cached.

    A symbol the provider did not return is negative-cached rather than blanked:
    the previous price stays readable and visibly stale, which is more useful
    than an empty row.
    """
    base = known or Quote(symbol=symbol, provider_symbol=provider_symbol)
    base = replace(base, provider_symbol=provider_symbol)

    if price is None:
        return replace(base, status=QuoteStatus.NOT_FOUND, fetched_at=now), (
            QuoteStatus.NOT_FOUND
        )

    updated = replace(
        base,
        price=_number(price.get("price")),
        prev_close=_number(price.get("prev_close")),
        currency=_currency(price.get("currency")) or base.currency,
        quote_time=_text(price.get("quote_time")),
        fetched_at=now,
        status=QuoteStatus.OK,
    )
    if meta is not None:
        updated = replace(
            updated,
            name=_text(meta.get("name")) or updated.name,
            sector=_text(meta.get("sector")) or updated.sector,
            exchange=_text(meta.get("exchange")) or updated.exchange,
            market_cap=_number(meta.get("market_cap")) or updated.market_cap,
            currency=_currency(meta.get("currency")) or updated.currency,
            meta_fetched_at=now,
            fundamentals=_fundamentals(meta),
        )
        splits = meta.get("splits")
        if splits is not None:
            updated = replace(updated, splits=splits)
    return updated, QuoteStatus.OK


def _tally(result: RefreshResult, outcome: QuoteStatus) -> RefreshResult:
    """Count one symbol's outcome into the running tally.

    Only `OK` and `NOT_FOUND` reach here: a failed symbol is counted before its
    row would be merged, since it has none to write.
    """
    if outcome is QuoteStatus.OK:
        return replace(result, fetched=result.fetched + 1)
    return replace(result, not_found=result.not_found + 1)


def _fill_gaps(
    symbols: Sequence[str],
    cached: Mapping[str, Quote],
    resolver: SymbolResolver,
) -> dict[str, Quote]:
    """Return a quote for every requested symbol, empty where there is none."""
    return {
        symbol: cached.get(
            symbol,
            Quote(symbol=symbol, provider_symbol=resolver.provider_symbol(symbol)),
        )
        for symbol in symbols
    }


def _fundamentals(values: Mapping[Any, Any]) -> Fundamentals:
    """Read fundamentals out of a mapping keyed by their field names."""
    read: dict[str, Any] = {}
    for item in fields(Fundamentals):
        raw = values.get(item.name)
        numeric = column_of(item).kind is Kind.NUMBER
        read[item.name] = _number(raw) if numeric else _text(raw)
    return Fundamentals(**read)


def _write_history(
    symbol: str,
    ranges: Mapping[PriceRange, RangeHistory],
    fetched_at: datetime,
) -> None:
    """Replace a symbol's stored history with a freshly condensed one."""
    quote_history.replace(
        symbol,
        [
            QuoteHistory(
                symbol=symbol,
                range=price_range,
                seq=seq,
                date=point.when,
                close=point.close,
                fetched_at=fetched_at,
            )
            for price_range, summary in ranges.items()
            for seq, point in enumerate((summary.anchor, *summary.points))
        ],
    )


def _read_history(
    symbols: Sequence[str],
) -> dict[str, tuple[datetime, dict[PriceRange, RangeHistory]]]:
    """Read stored history back, with when each symbol's was fetched."""
    grouped: dict[str, dict[PriceRange, list[PricePoint]]] = {}
    fetched: dict[str, datetime] = {}
    for stored in quote_history.read(symbols):
        symbol = stored.symbol
        # The oldest stamp wins, so a part-written symbol reads as due.
        fetched[symbol] = min(fetched.get(symbol, stored.fetched_at), stored.fetched_at)
        point = PricePoint(stored.date, stored.close)
        grouped.setdefault(symbol, {}).setdefault(stored.range, []).append(point)

    return {
        symbol: (
            fetched[symbol],
            {
                price_range: RangeHistory(price_range, points[0], tuple(points[1:]))
                for price_range, points in ranges.items()
            },
        )
        for symbol, ranges in grouped.items()
        if symbol in fetched
    }
