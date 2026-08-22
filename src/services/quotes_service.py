"""Market quotes from Yahoo Finance, cached in the folio database.

`import yfinance` is deliberately lazy, inside the two fetch methods. It is a
heavy import, most commands never price anything, and `--offline` must be able
to run without the package resolving at all.
"""

from __future__ import annotations

import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app import get_config
from db.queries import (
    delete_rows,
    get_connection,
    get_rows,
    get_tables,
    insert_or_replace_many,
)
from db.schema import create_quotes_table
from utils.constants import Column, Currency, QuoteStatus, Table
from utils.numeric import dec

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
    from decimal import Decimal

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
        meta_fetched_at: When the metadata was last fetched. Its own TTL.
        status: How the last fetch attempt came out.
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

    @property
    def meta_is_stale(self) -> bool:
        """Whether the name, sector and market cap are due a refetch."""
        if self.meta_fetched_at is None:
            return True
        elapsed = datetime.now(UTC) - self.meta_fetched_at
        return elapsed > timedelta(days=get_config().quotes_metadata_ttl_days)

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
            with get_connection() as conn:
                if Table.QUOTES not in get_tables(conn):
                    return {}
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
    ) -> dict[str, Quote]:
        """Return a quote for every symbol, fetching only what is due.

        Args:
            symbols: Canonical folio symbols to price.
            resolver: Supplies the provider spelling for each symbol.
            refresh: Refetch every symbol regardless of its TTL.
            offline: Never touch the network. Returns whatever is cached,
                including nothing.

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

        due = [symbol for symbol in wanted if refresh or _is_due(cached.get(symbol))]
        if due:
            cls.refresh(due, resolver, cached=cached)
            cached = cls.cached(wanted)

        return _fill_gaps(wanted, cached, resolver)

    @classmethod
    def refresh(
        cls,
        symbols: Sequence[str],
        resolver: SymbolResolver,
        cached: Mapping[str, Quote] | None = None,
    ) -> RefreshResult:
        """Refetch the given symbols and write them to the cache.

        Args:
            symbols: Canonical folio symbols to refetch.
            resolver: Supplies the provider spelling for each symbol.
            cached: Already-read cached rows, to save a second query.

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

        metadata = cls._collect_metadata(wanted, ysymbols, known, prices)

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
    ) -> dict[str, dict]:
        """Fetch metadata for the symbols whose slower TTL has expired.

        `.info` is a full scrape per symbol and heavily rate-limited, so it runs
        once on first sight of a symbol and roughly monthly after. A failure
        here costs a name, never a price.
        """
        due = [
            ysymbols[symbol]
            for symbol in symbols
            # Nothing to describe if the symbol did not price at all.
            if ysymbols[symbol] in prices
            and (
                symbol not in known
                or known[symbol].meta_is_stale
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
        try:
            with get_connection() as conn:
                if Table.QUOTES not in get_tables(conn):
                    return 0
                if symbols is None:
                    return delete_rows(conn, Table.QUOTES)
                wanted = [symbol.upper() for symbol in symbols]
                if not wanted:
                    return 0
                placeholders = ", ".join("?" for _ in wanted)
                return delete_rows(
                    conn,
                    Table.QUOTES,
                    where=f'"{Column.Quote.SYMBOL}" IN ({placeholders})',
                    params=wanted,
                )
        except sqlite3.Error as error:
            logger.debug("Could not clear the quotes cache: %s", error)
            return 0

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
                "prev_close": _attr(fast, "previous_close"),
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
            closes = _daily_closes(frame, ysymbol, single=len(ysymbols) == 1)
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
        """Fetch name, sector, exchange and market cap for a batch.

        Args:
            ysymbols: Provider spellings to describe.

        Returns:
            Each provider symbol mapped to its raw metadata fields.
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
            }

        return dict(_run_concurrently(_describe, ysymbols))


def _attr(source: object, name: str) -> object | None:
    """Read one attribute off a provider object without trusting it exists."""
    try:
        return getattr(source, name, None)
    except Exception:  # noqa: BLE001 - fast_info computes lazily and can raise
        return None


def _daily_closes(frame: Any, ysymbol: str, *, single: bool) -> list[float]:  # noqa: ANN401
    """Pull the close series for one symbol out of a batched download."""
    try:
        column = frame["Close"] if single else frame[ysymbol]["Close"]
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
    }


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
        logger.warning("Could not write the quotes cache: %s", error)
