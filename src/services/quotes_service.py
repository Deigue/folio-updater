"""Market quotes from Yahoo Finance, cached in the folio database.

`import yfinance` is deliberately lazy, inside the two fetch methods. It is a
heavy import, most commands never price anything, and `--offline` must be able
to run without the package resolving at all.
"""

from __future__ import annotations

import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app import get_config
from db.queries import (
    delete_rows,
    get_connection,
    get_rows,
    get_tables,
    insert_or_replace_many,
    replace_rows,
)
from db.schema import create_quote_history_table, create_quotes_table
from domain import TORONTO_TZ, Column, Currency, PriceRange, QuoteStatus, Table
from domain.numeric import dec
from services.price_history import (
    PricePoint,
    RangeHistory,
    condense,
    condense_intraday,
    months_back,
)
from term import announce

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence

    from services.symbols import SymbolResolver

logger = logging.getLogger(__name__)

SOURCE = "yfinance"
NOT_FOUND_TTL_DAYS = 7
_RATE_LIMIT_MARKERS = ("rate limit", "too many requests", "429")

# Per-symbol fetches are network-bound, not CPU-bound, capped concurrent workers
# so that a varied folio doesn't set off hundred connections.
_MAX_FETCH_WORKERS = 8


def _run_concurrently[T](
    fetch: Callable[[str], T | None],
    ysymbols: Sequence[str],
) -> list[T]:
    """Run a per-symbol fetch across a thread pool, dropping empty results.

    Args:
        fetch: Looks up one provider symbol; returns None on a miss.
        ysymbols: Provider spellings to fetch.

    Returns:
        Whatever `fetch` returned for each symbol, in no particular order,
        with the None misses already filtered out.
    """
    if not ysymbols:
        return []
    workers = min(len(ysymbols), _MAX_FETCH_WORKERS)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(fetch, ysymbols))
    return [result for result in results if result is not None]


@dataclass(frozen=True)
class Fundamentals:
    """Details from provider for a given security beyond its price.

    Attributes:
        quote_type: The provider's kind of security: EQUITY, ETF, MUTUALFUND...
        trailing_pe: Price over the last twelve months' earnings.
        forward_pe: Price over the next twelve months' expected earnings.
        eps: Earnings per share over the last twelve months.
        beta: Volatility against the market. A fund's three-year beta.
        dividend_rate: Annual dividend per share.
        dividend_yield: Annual dividend over price, as a ratio.
        last_dividend: The most recent dividend per share.
        ex_dividend_date: The next or most recent ex-dividend date.
        earnings_date: The next earnings date, when the provider knows it.
        high_52: The 52-week high.
        low_52: The 52-week low.
        avg_50: The 50-day average price.
        avg_200: The 200-day average price.
        volume: The last session's volume.
        avg_volume: Average daily volume.
        expense_ratio: A fund's expense ratio, as a ratio.
        total_assets: A fund's assets under management.
        category: A fund's category.
        fund_family: A fund's manager.
    """

    quote_type: str | None = None
    trailing_pe: Decimal | None = None
    forward_pe: Decimal | None = None
    eps: Decimal | None = None
    beta: Decimal | None = None
    dividend_rate: Decimal | None = None
    dividend_yield: Decimal | None = None
    last_dividend: Decimal | None = None
    ex_dividend_date: str | None = None
    earnings_date: str | None = None
    high_52: Decimal | None = None
    low_52: Decimal | None = None
    avg_50: Decimal | None = None
    avg_200: Decimal | None = None
    volume: Decimal | None = None
    avg_volume: Decimal | None = None
    expense_ratio: Decimal | None = None
    total_assets: Decimal | None = None
    category: str | None = None
    fund_family: str | None = None


# Where each fundamental is stored, and whether it is a number or text.
_FUNDAMENTAL_COLUMNS: dict[str, tuple[Column.Quote, bool]] = {
    "quote_type": (Column.Quote.QUOTE_TYPE, False),
    "trailing_pe": (Column.Quote.TRAILING_PE, True),
    "forward_pe": (Column.Quote.FORWARD_PE, True),
    "eps": (Column.Quote.EPS, True),
    "beta": (Column.Quote.BETA, True),
    "dividend_rate": (Column.Quote.DIVIDEND_RATE, True),
    "dividend_yield": (Column.Quote.DIVIDEND_YIELD, True),
    "last_dividend": (Column.Quote.LAST_DIVIDEND, True),
    "ex_dividend_date": (Column.Quote.EX_DIVIDEND_DATE, False),
    "earnings_date": (Column.Quote.EARNINGS_DATE, False),
    "high_52": (Column.Quote.HIGH_52, True),
    "low_52": (Column.Quote.LOW_52, True),
    "avg_50": (Column.Quote.AVG_50, True),
    "avg_200": (Column.Quote.AVG_200, True),
    "volume": (Column.Quote.VOLUME, True),
    "avg_volume": (Column.Quote.AVG_VOLUME, True),
    "expense_ratio": (Column.Quote.EXPENSE_RATIO, True),
    "total_assets": (Column.Quote.TOTAL_ASSETS, True),
    "category": (Column.Quote.CATEGORY, False),
    "fund_family": (Column.Quote.FUND_FAMILY, False),
}


@dataclass(frozen=True)
class Quote:
    """A symbol's latest cached market snapshot.

    Attributes:
        symbol: The folio's own canonical symbol.
        ysymbol: How the provider spells it.
        price: Last or current price, in `currency`. None when unpriced.
        prev_close: Previous close, which is what `change` is measured from.
        currency: The currency the price is quoted in.
        name: Short name from the provider.
        sector: Sector from the provider.
        exchange: Listing exchange from the provider.
        market_cap: Market capitalisation, in `currency`.
        quote_time: The provider's own market timestamp.
        fetched_at: When the price was last fetched. Drives the TTL.
        meta_fetched_at: When the metadata, fundamentals included, was last
            fetched. Its own TTL.
        status: How the last fetch attempt came out.
        fundamentals: Valuation, dividend, trading-range and fund figures.
    """

    symbol: str
    ysymbol: str
    price: Decimal | None = None
    prev_close: Decimal | None = None
    currency: Currency | None = None
    name: str | None = None
    sector: str | None = None
    exchange: str | None = None
    market_cap: Decimal | None = None
    quote_time: str | None = None
    fetched_at: datetime | None = None
    meta_fetched_at: datetime | None = None
    status: QuoteStatus = QuoteStatus.ERROR
    fundamentals: Fundamentals = field(default_factory=Fundamentals)

    @property
    def age(self) -> timedelta | None:
        """How long ago the price was fetched, or None if it never was."""
        if self.fetched_at is None:
            return None
        return datetime.now(UTC) - self.fetched_at

    @property
    def is_stale(self) -> bool:
        """Whether the price is older than the configured TTL."""
        age = self.age
        if age is None:
            return True
        return age > timedelta(minutes=get_config().quotes_ttl_minutes)

    def meta_older_than(self, max_age: timedelta) -> bool:
        """Whether the metadata is older than `max_age`, or was never fetched.

        Args:
            max_age: How old the metadata may be and still be used.

        Returns:
            True when a refetch is due.
        """
        if self.meta_fetched_at is None:
            return True
        return datetime.now(UTC) - self.meta_fetched_at > max_age

    @property
    def priced(self) -> bool:
        """Whether this quote can be used to value a position."""
        return (
            self.status is QuoteStatus.OK and self.price is not None and self.price > 0
        )

    @property
    def day_change(self) -> Decimal | None:
        """The day's per-share move, or None when either side is missing."""
        if self.price is None or self.prev_close is None:
            return None
        return self.price - self.prev_close

    @property
    def from_high_52(self) -> Decimal | None:
        """How far the price sits from its 52-week high: -0.07 is 7% below it."""
        high = self.fundamentals.high_52
        if self.price is None or not high:
            return None
        return self.price / high - 1

    @property
    def day_change_pct(self) -> Decimal | None:
        """The day's move as a share of the previous close, or None without one."""
        change = self.day_change
        if change is None or not self.prev_close:
            return None
        return change / self.prev_close


@dataclass(frozen=True)
class RefreshResult:
    """What one refresh pass did.

    Attributes:
        fetched: Symbols priced successfully.
        failed: Symbols whose fetch errored, leaving any cached row in place.
        not_found: Symbols the provider does not know.
        used_fallback: Whether throttling forced the batched daily-close path,
            which yields the previous close rather than an intraday last.
    """

    fetched: int = 0
    failed: int = 0
    not_found: int = 0
    used_fallback: bool = False


def _is_rate_limited(error: Exception) -> bool:
    """Whether an exception reads as provider throttling."""
    message = str(error).lower()
    return any(marker in message for marker in _RATE_LIMIT_MARKERS)


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


def _moment(value: object) -> datetime | None:
    """Read an ISO-8601 timestamp back."""
    text = _text(value)
    if text is None:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _currency(value: object) -> Currency | None:
    """Read a currency cell, or None when it unsupported by the app."""
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
        try:
            # Brings a cache of an older shape up to date before it is read.
            create_quotes_table()
            with get_connection() as conn:
                frame = get_rows(conn, Table.QUOTES)
        except sqlite3.Error as error:
            logger.debug("Could not read the quotes cache: %s", error)
            return {}

        if frame.empty:
            return {}

        wanted = {symbol.upper() for symbol in symbols} if symbols is not None else None
        quotes: dict[str, Quote] = {}
        for record in frame.to_dict("records"):
            symbol = _text(record.get(Column.Quote.SYMBOL))
            if symbol is None or (wanted is not None and symbol not in wanted):
                continue
            quotes[symbol] = _quote_from_record(symbol, record)
        return quotes

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
        wanted = [symbol.upper() for symbol in dict.fromkeys(symbols)]
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
        if due:
            cls.refresh(due, resolver, cached=cached, fundamentals=fundamentals)
            cached = cls.cached(wanted)

        return _fill_gaps(wanted, cached, resolver)

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
        wanted = [symbol.upper() for symbol in dict.fromkeys(symbols)]
        if not wanted:
            return RefreshResult()

        known = dict(cached) if cached is not None else cls.cached(wanted)
        ysymbols = {symbol: resolver.yahoo_symbol(symbol) for symbol in wanted}

        used_fallback = False
        try:
            prices = cls._fetch_prices(list(ysymbols.values()))
        except Exception as error:  # noqa: BLE001 - provider raises freely
            if not _is_rate_limited(error):
                logger.warning(
                    "Could not fetch quotes (%s); using what is cached",
                    error,
                )
                return RefreshResult(failed=len(wanted))
            logger.info("Quote provider is throttling; falling back to daily closes")
            used_fallback = True
            try:
                prices = cls._fetch_daily(list(ysymbols.values()))
            except Exception as fallback_error:  # noqa: BLE001
                logger.warning(
                    "Quote fallback also failed (%s); using what is cached",
                    fallback_error,
                )
                return RefreshResult(failed=len(wanted))

        metadata = cls._collect_metadata(
            wanted,
            ysymbols,
            known,
            prices,
            _meta_max_age(fundamentals=fundamentals),
        )

        now = datetime.now(UTC)
        result = RefreshResult(used_fallback=used_fallback)
        rows: list[dict[str, Any]] = []
        for symbol in wanted:
            quote, outcome = _merge(
                symbol,
                ysymbols[symbol],
                known.get(symbol),
                prices.get(ysymbols[symbol]),
                metadata.get(ysymbols[symbol]),
                now,
            )
            rows.append(_record(quote))
            result = _tally(result, outcome)

        _write(rows)
        return result

    @classmethod
    def _collect_metadata(
        cls,
        symbols: Sequence[str],
        ysymbols: Mapping[str, str],
        known: Mapping[str, Quote],
        prices: Mapping[str, dict],
        max_age: timedelta,
    ) -> dict[str, dict]:
        """Fetch metadata for the symbols whose slower TTL has expired.

        `.info` is a full scrape per symbol and heavily rate-limited, so it runs
        once on first sight of a symbol and roughly monthly after, or daily for
        a caller showing fundamentals.
        """
        due = [
            ysymbols[symbol]
            for symbol in symbols
            # Nothing to describe if the symbol did not price at all.
            if ysymbols[symbol] in prices
            and (
                symbol not in known
                or known[symbol].meta_older_than(max_age)
                or known[symbol].name is None
            )
        ]
        if not due:
            return {}

        try:
            return cls._fetch_metadata(due)
        except Exception as error:  # noqa: BLE001 - metadata is best-effort
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
        placeholders = ", ".join("?" for _ in wanted or ())

        def matching(column: str) -> str | None:
            """Narrow a delete to the symbols asked for, or None for every row."""
            return None if wanted is None else f'"{column}" IN ({placeholders})'

        try:
            # A symbol's history goes with its quote, so both tables are cleared.
            create_quote_history_table()
            with get_connection() as conn:
                delete_rows(
                    conn,
                    Table.QUOTE_HISTORY,
                    where=matching(Column.QuoteHistory.SYMBOL),
                    params=wanted,
                )
                if Table.QUOTES not in get_tables(conn):
                    return 0
                return delete_rows(
                    conn,
                    Table.QUOTES,
                    where=matching(Column.Quote.SYMBOL),
                    params=wanted,
                )
        except sqlite3.Error as error:
            logger.debug("Could not clear the quotes cache: %s", error)
            return 0

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
        anchor to the live price, so the moves stay current between fetches.

        Args:
            symbols: Canonical folio symbols.
            resolver: Supplies the provider spelling for each symbol.
            refresh: Refetch every symbol, however recently it was fetched.
            offline: Never touch the network; return whatever is stored.

        Returns:
            Each symbol with any history mapped to its ranges. A symbol the
            provider has no history for is absent.
        """
        wanted = [symbol.upper() for symbol in dict.fromkeys(symbols)]
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
        """Return the `2h` and `1d` ranges, live. These are never stored.

        Args:
            symbols: Canonical folio symbols.
            resolver: Supplies the provider spelling for each symbol.
            offline: Never touch the network, which leaves nothing to return.

        Returns:
            Each symbol the provider had bars for, mapped to its ranges.
        """
        wanted = [symbol.upper() for symbol in dict.fromkeys(symbols)]
        if offline or not wanted:
            return {}
        ysymbols = {resolver.yahoo_symbol(symbol): symbol for symbol in wanted}
        try:
            fetched = cls._fetch_intraday(list(ysymbols))
        except Exception as error:  # noqa: BLE001 - the provider raises freely
            logger.debug("Could not fetch intraday bars: %s", error)
            return {}
        return {
            ysymbols[ysymbol]: ranges
            for ysymbol, ranges in fetched.items()
            if ysymbol in ysymbols
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
        ysymbols = {resolver.yahoo_symbol(symbol): symbol for symbol in symbols}
        try:
            fetched = cls._fetch_history(list(ysymbols), today=today)
        except Exception as error:  # noqa: BLE001 - the provider raises freely
            logger.warning(
                "Could not fetch price history (%s); using what is kept",
                error,
            )
            return {}

        now = datetime.now(UTC)
        written: dict[str, tuple[datetime, dict[PriceRange, RangeHistory]]] = {}
        for ysymbol, ranges in fetched.items():
            symbol = ysymbols[ysymbol]
            _write_history(symbol, ranges, now)
            written[symbol] = (now, ranges)
        return written

    # -- NETWORK ---------------------------------------------------------

    @classmethod
    def _fetch_prices(cls, ysymbols: Sequence[str]) -> dict[str, dict]:
        """Fetch last price and previous close for a batch of symbols.

        Args:
            ysymbols: Provider spellings to price.

        Returns:
            Each provider symbol mapped to its raw price fields. A symbol the
            provider does not know is simply absent.
        """
        import yfinance as yf  # noqa: PLC0415 - heavy, and unused when offline

        tickers = yf.Tickers(" ".join(ysymbols))
        now = datetime.now(UTC).isoformat()

        def _price(ysymbol: str) -> tuple[str, dict] | None:
            ticker = tickers.tickers.get(ysymbol)
            if ticker is None:
                return None
            fast = ticker.fast_info
            price = _attr(fast, "last_price")
            if price is None:
                return None
            return ysymbol, {
                "price": price,
                "prev_close": _previous_close(fast),
                "currency": _attr(fast, "currency"),
                "quote_time": now,
            }

        return dict(_run_concurrently(_price, ysymbols))

    @classmethod
    def _fetch_daily(cls, ysymbols: Sequence[str]) -> dict[str, dict]:
        """Fetch yesterday's close for a batch, in one request.

        The throttling fallback. One download covers every symbol, at the cost
        of a daily close instead of an intraday last.

        Args:
            ysymbols: Provider spellings to price.

        Returns:
            Each provider symbol mapped to its raw price fields.
        """
        import yfinance as yf  # noqa: PLC0415 - heavy, and unused when offline

        frame = yf.download(
            list(ysymbols),
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
        for ysymbol in ysymbols:
            closes = _daily_closes(frame, ysymbol)
            if not closes:
                continue
            fetched[ysymbol] = {
                "price": closes[-1],
                "prev_close": closes[-2] if len(closes) > 1 else None,
                "currency": None,
                "quote_time": datetime.now(UTC).isoformat(),
            }
        return fetched

    @classmethod
    def _fetch_metadata(cls, ysymbols: Sequence[str]) -> dict[str, dict]:
        """Fetch name, sector, exchange, market cap and fundamentals for a batch.

        Args:
            ysymbols: Provider spellings to describe.

        Returns:
            Each provider symbol mapped to its metadata fields, the
            fundamentals keyed by their `Fundamentals` field names.
        """
        import yfinance as yf  # noqa: PLC0415 - heavy, and unused when offline

        def _describe(ysymbol: str) -> tuple[str, dict] | None:
            try:
                info = yf.Ticker(ysymbol).info
            except Exception as error:  # noqa: BLE001 - per-symbol scrape
                logger.debug("No metadata for %s: %s", ysymbol, error)
                return None
            if not info:
                return None
            return ysymbol, {
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
            }

        return dict(_run_concurrently(_describe, ysymbols))

    @classmethod
    def _fetch_history(
        cls,
        ysymbols: Sequence[str],
        *,
        today: date,
    ) -> dict[str, dict[PriceRange, RangeHistory]]:
        """Fetch and condense a batch's history, in four downloads.

        Daily closes for the last year, weekly for five, monthly for the whole
        listing, and hourly bars for the last month's shape: enough for every
        stored range, and small whatever a symbol's age. The raw frames are
        condensed here and never kept.

        Args:
            ysymbols: Provider spellings to fetch.
            today: The day the ranges are measured back from.

        Returns:
            Each provider symbol mapped to its condensed ranges.
        """
        # A margin either side of the earliest anchor, for holidays and gaps.
        margin = timedelta(days=14)
        daily_from = min(months_back(today, 12), date(today.year - 1, 12, 31))
        frames = {
            "daily": _download(
                ysymbols,
                interval="1d",
                start=(daily_from - margin).isoformat(),
            ),
            "weekly": _download(
                ysymbols,
                interval="1wk",
                start=(months_back(today, 60) - margin).isoformat(),
            ),
            "monthly": _download(ysymbols, interval="1mo", period="max"),
            "hourly": _download(
                ysymbols,
                interval="60m",
                start=(months_back(today, 1) - margin).isoformat(),
            ),
        }

        condensed: dict[str, dict[PriceRange, RangeHistory]] = {}
        for ysymbol in ysymbols:
            ranges = condense(
                _dated_closes(frames["daily"], ysymbol),
                _dated_closes(frames["weekly"], ysymbol),
                _dated_closes(frames["monthly"], ysymbol),
                today=today,
                hourly=_dated_closes(frames["hourly"], ysymbol, intraday=True),
            )
            if ranges:
                condensed[ysymbol] = ranges
        return condensed

    @classmethod
    def _fetch_intraday(
        cls,
        ysymbols: Sequence[str],
    ) -> dict[str, dict[PriceRange, RangeHistory]]:
        """Fetch and condense a batch's bars over the last week of sessions.

        One-minute bars for `2h`, five-minute bars for the rest. Regular
        sessions only: no pre- or after-market trading.

        Args:
            ysymbols: Provider spellings to fetch.

        Returns:
            Each provider symbol mapped to its `2h`, `1d`, `2d` and `1wk` ranges.
        """
        minutes = _download(ysymbols, interval="1m", period="5d")
        fives = _download(ysymbols, interval="5m", period="5d")
        week_from = datetime.now(TORONTO_TZ).date() - timedelta(days=7)
        condensed: dict[str, dict[PriceRange, RangeHistory]] = {}
        for ysymbol in ysymbols:
            ranges = condense_intraday(
                _dated_closes(minutes, ysymbol, intraday=True),
                _dated_closes(fives, ysymbol, intraday=True),
                week_from=week_from,
            )
            if ranges:
                condensed[ysymbol] = ranges
        return condensed


def _download(ysymbols: Sequence[str], **window: str) -> Any:  # noqa: ANN401 - pandas
    """Download a batch's bars over one window, grouped by symbol.

    Args:
        ysymbols: Provider spellings to fetch.
        **window: The `interval`, and a `start` or a `period`.

    Returns:
        The provider's frame, or None when it returned nothing.
    """
    import yfinance as yf  # noqa: PLC0415 - heavy, and unused when offline

    return yf.download(
        list(ysymbols),
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

    `previous_close` - groups a week of *pre/post-market* hourly bars by calendar
    date and takes the last bar of the day before. (counting extended-hours)

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
    except Exception:  # noqa: BLE001 - fast_info computes lazily and can raise
        return None


def _dated_closes(
    frame: Any,  # noqa: ANN401 - a pandas frame from the provider
    ysymbol: str,
    *,
    intraday: bool = False,
) -> list[PricePoint]:
    """Pull one symbol's closes, with their dates, out of a batched download.

    Args:
        frame: The provider's download, or None when it returned nothing.
        ysymbol: The provider symbol to read.
        intraday: Stamp each close with its UTC time rather than its date.

    Returns:
        The closes, oldest first.
    """
    if frame is None or frame.empty:
        return []
    try:
        closes = _close_column(frame, ysymbol).dropna()
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


def _close_column(frame: Any, ysymbol: str) -> Any:  # noqa: ANN401 - pandas
    """Find one symbol's closes in a download, whatever shape it came in.

    Grouped by ticker, a download keys its columns by symbol and then field,
    and yfinance keeps that shape even for a single symbol. A flat frame, with
    the fields alone, can only hold one symbol, so it is read as that one.
    """
    if frame.columns.nlevels > 1:
        return frame[ysymbol]["Close"]
    return frame["Close"]


def _daily_closes(frame: Any, ysymbol: str) -> list[float]:  # noqa: ANN401
    """Pull the close series for one symbol out of a batched download."""
    try:
        column = _close_column(frame, ysymbol)
    except (KeyError, TypeError, IndexError):
        return []
    return [float(value) for value in column.dropna().tolist()]


def _is_due(quote: Quote | None) -> bool:
    """Whether a cached quote should be refetched now."""
    if quote is None:
        return True
    if quote.status is QuoteStatus.NOT_FOUND:
        # Negative-cached: re-ask occasionally, not every run.
        age = quote.age
        return age is None or age > timedelta(days=NOT_FOUND_TTL_DAYS)
    return quote.is_stale


def _merge(  # noqa: PLR0913, PLR0917
    symbol: str,
    ysymbol: str,
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
    base = known or Quote(symbol=symbol, ysymbol=ysymbol)
    base = replace(base, ysymbol=ysymbol)

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
    return updated, QuoteStatus.OK


def _tally(result: RefreshResult, outcome: QuoteStatus) -> RefreshResult:
    """Count one symbol's outcome into the running tally.

    Only `OK` and `NOT_FOUND` reach here. The provider is called once per batch,
    so a fetch that errored fails every symbol at once and returns before any
    row is merged.
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
            Quote(symbol=symbol, ysymbol=resolver.yahoo_symbol(symbol)),
        )
        for symbol in symbols
    }


def _quote_from_record(symbol: str, record: Mapping[Hashable, Any]) -> Quote:
    """Build a `Quote` from one cached database row."""
    status = _text(record.get(Column.Quote.STATUS))
    try:
        resolved = QuoteStatus(status) if status else QuoteStatus.ERROR
    except ValueError:
        resolved = QuoteStatus.ERROR
    return Quote(
        symbol=symbol,
        ysymbol=_text(record.get(Column.Quote.YSYMBOL)) or symbol,
        price=_number(record.get(Column.Quote.PRICE)),
        prev_close=_number(record.get(Column.Quote.PREV_CLOSE)),
        currency=_currency(record.get(Column.Quote.CURRENCY)),
        name=_text(record.get(Column.Quote.NAME)),
        sector=_text(record.get(Column.Quote.SECTOR)),
        exchange=_text(record.get(Column.Quote.EXCHANGE)),
        market_cap=_number(record.get(Column.Quote.MARKET_CAP)),
        quote_time=_text(record.get(Column.Quote.QUOTE_TIME)),
        fetched_at=_moment(record.get(Column.Quote.FETCHED_AT)),
        meta_fetched_at=_moment(record.get(Column.Quote.META_FETCHED_AT)),
        status=resolved,
        fundamentals=_fundamentals(
            {
                name: record.get(column)
                for name, (column, _) in _FUNDAMENTAL_COLUMNS.items()
            },
        ),
    )


def _record(quote: Quote) -> dict[str, Any]:
    """Flatten a `Quote` into the columns the cache table holds."""
    return {
        str(Column.Quote.SYMBOL): quote.symbol,
        str(Column.Quote.YSYMBOL): quote.ysymbol,
        str(Column.Quote.PRICE): _stored(quote.price),
        str(Column.Quote.PREV_CLOSE): _stored(quote.prev_close),
        str(Column.Quote.CURRENCY): str(quote.currency) if quote.currency else None,
        str(Column.Quote.NAME): quote.name,
        str(Column.Quote.SECTOR): quote.sector,
        str(Column.Quote.EXCHANGE): quote.exchange,
        str(Column.Quote.MARKET_CAP): _stored(quote.market_cap),
        str(Column.Quote.QUOTE_TIME): quote.quote_time,
        str(Column.Quote.FETCHED_AT): _iso(quote.fetched_at),
        str(Column.Quote.META_FETCHED_AT): _iso(quote.meta_fetched_at),
        str(Column.Quote.SOURCE): SOURCE,
        str(Column.Quote.STATUS): str(quote.status),
        **{
            str(column): (
                _stored(getattr(quote.fundamentals, name))
                if numeric
                else getattr(quote.fundamentals, name)
            )
            for name, (column, numeric) in _FUNDAMENTAL_COLUMNS.items()
        },
    }


def _fundamentals(values: Mapping[Any, Any]) -> Fundamentals:
    """Read fundamentals out of a mapping keyed by their field names."""
    read: dict[str, Any] = {}
    for item in fields(Fundamentals):
        _, numeric = _FUNDAMENTAL_COLUMNS[item.name]
        raw = values.get(item.name)
        read[item.name] = _number(raw) if numeric else _text(raw)
    return Fundamentals(**read)


def _stored(value: Decimal | None) -> float | None:
    """Render a Decimal for sqlite, keeping None a genuine null."""
    return None if value is None else float(value)


def _iso(moment: datetime | None) -> str | None:
    """Render a timestamp for storage, keeping None a genuine null."""
    return None if moment is None else moment.isoformat()


def _write(rows: list[dict[str, Any]]) -> None:
    """Persist refreshed quotes, creating the table on first use."""
    if not rows:
        return
    try:
        create_quotes_table()
        with get_connection() as conn:
            insert_or_replace_many(conn, Table.QUOTES, rows)
    except sqlite3.Error as error:
        announce.warning(f"Could not write the quotes cache: {error}")


def _write_history(
    symbol: str,
    ranges: Mapping[PriceRange, RangeHistory],
    fetched_at: datetime,
) -> None:
    """Replace a symbol's stored history with a freshly condensed one."""
    stamp = fetched_at.isoformat()
    rows = [
        {
            str(Column.QuoteHistory.SYMBOL): symbol,
            str(Column.QuoteHistory.RANGE): str(price_range),
            str(Column.QuoteHistory.SEQ): seq,
            str(Column.QuoteHistory.DATE): point.when,
            str(Column.QuoteHistory.CLOSE): float(point.close),
            str(Column.QuoteHistory.FETCHED_AT): stamp,
        }
        for price_range, summary in ranges.items()
        for seq, point in enumerate((summary.anchor, *summary.points))
    ]
    try:
        create_quote_history_table()
        with get_connection() as conn:
            replace_rows(
                conn,
                Table.QUOTE_HISTORY,
                rows,
                where=f'"{Column.QuoteHistory.SYMBOL}" = ?',
                params=[symbol],
            )
    except sqlite3.Error as error:
        announce.warning(f"Could not write the price history: {error}")


def _read_history(
    symbols: Sequence[str],
) -> dict[str, tuple[datetime, dict[PriceRange, RangeHistory]]]:
    """Read stored history back, with when each symbol's was fetched."""
    placeholders = ", ".join("?" for _ in symbols)
    try:
        create_quote_history_table()
        with get_connection() as conn:
            frame = get_rows(
                conn,
                Table.QUOTE_HISTORY,
                where=f'"{Column.QuoteHistory.SYMBOL}" IN ({placeholders})',
                params=list(symbols),
                order_by=(
                    f'"{Column.QuoteHistory.SYMBOL}", "{Column.QuoteHistory.RANGE}", '
                    f'"{Column.QuoteHistory.SEQ}"'
                ),
            )
    except sqlite3.Error as error:
        logger.debug("Could not read the price history: %s", error)
        return {}

    grouped: dict[str, dict[PriceRange, list[PricePoint]]] = {}
    fetched: dict[str, datetime] = {}
    for record in frame.to_dict("records"):
        symbol = str(record[Column.QuoteHistory.SYMBOL])
        moment = _moment(record[Column.QuoteHistory.FETCHED_AT])
        if moment is not None:
            # The oldest stamp wins, so a part-written symbol reads as due.
            fetched[symbol] = min(fetched.get(symbol, moment), moment)
        price_range = PriceRange(str(record[Column.QuoteHistory.RANGE]))
        point = PricePoint(
            str(record[Column.QuoteHistory.DATE]),
            dec(record[Column.QuoteHistory.CLOSE]),
        )
        grouped.setdefault(symbol, {}).setdefault(price_range, []).append(point)

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
