"""Market quotes from the quote provider, cached in the folio database."""

from __future__ import annotations

import logging
from dataclasses import dataclass, fields, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar

from app import get_config
from db import quote_history
from db import quotes as quote_store
from domain import TORONTO_TZ, Currency, PriceRange, QuoteStatus
from models import Fundamentals, Quote, QuoteHistory
from models.columns import Kind, column_of
from services.price_history import (
    PricePoint,
    RangeHistory,
    condense,
    condense_intraday,
)
from services.quote_provider import ProviderPrice, read_number, read_text
from services.yahoo_provider import YahooProvider

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from services.quote_provider import QuoteProvider

logger = logging.getLogger(__name__)

NOT_FOUND_TTL_DAYS = 7


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


def _currency(value: object) -> Currency | None:
    """Read a currency cell, or None when the app does not support it."""
    text = read_text(value)
    if text is None:
        return None
    try:
        return Currency(text.upper())
    except ValueError:
        # A quote in a currency we cannot recognize as yet.
        logger.debug("Quote carries unsupported currency '%s'", text)
        return None


class QuoteService:
    """Fetch, cache and read market quotes."""

    # The provider every fetch goes to. Tests stand a fake one in its place.
    provider: ClassVar[QuoteProvider] = YahooProvider()

    @staticmethod
    def cached(symbols: Iterable[str] | None = None) -> dict[str, Quote]:
        """Read cached quotes straight from the database.

        Args:
            symbols: Symbols to read, or None for every cached row.

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
        *,
        refresh: bool = False,
        offline: bool = False,
        fundamentals: bool = False,
    ) -> dict[str, Quote]:
        """Return a quote for every symbol, fetching only what is due.

        Args:
            symbols: Symbols to price.
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
            return cls._fill_gaps(wanted, cached)

        max_age = _meta_max_age(fundamentals=fundamentals)
        due = [
            symbol
            for symbol in wanted
            if refresh
            or _is_due(cached.get(symbol))
            or (fundamentals and cached[symbol].meta_older_than(max_age))
        ]
        if not due:
            return cls._fill_gaps(wanted, cached)

        result = cls.refresh(due, cached=cached, fundamentals=fundamentals)
        quotes = cls._fill_gaps(wanted, cls.cached(wanted))
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
        cached: Mapping[str, Quote] | None = None,
        *,
        fundamentals: bool = False,
    ) -> RefreshResult:
        """Refetch the given symbols and write them to the cache.

        Args:
            symbols: Symbols to refetch.
            cached: Already-read cached rows, to save a second query.
            fundamentals: Hold the metadata to the fundamentals TTL.

        Returns:
            A tally of what happened.
        """
        wanted = _unique_upper(symbols)
        if not wanted:
            return RefreshResult()
        provider = cls.provider
        if not provider.reachable():
            return RefreshResult(failed=frozenset(wanted))

        known = dict(cached) if cached is not None else cls.cached(wanted)
        provider_symbols = {symbol: provider.spell(symbol) for symbol in wanted}

        fetch = provider.prices(list(provider_symbols.values()))
        prices = dict(fetch.prices)
        failed = set(fetch.failed)
        if fetch.throttled:
            logger.info("Quote provider is throttling; falling back to daily closes")
            daily = cls._price_from_closes(fetch.throttled)
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
            refreshed.append(replace(quote, source=provider.name))
            result = _tally(result, outcome)

        quote_store.write(refreshed)
        return result

    @classmethod
    def _price_from_closes(
        cls,
        provider_symbols: Iterable[str],
    ) -> dict[str, ProviderPrice]:
        """Price throttled symbols from their daily closes, or none of them.

        One download covers every symbol, at the cost of a daily close instead
        of an intraday price, and no currency.
        """
        try:
            closes = cls.provider.daily_closes(sorted(provider_symbols))
        except Exception as error:  # noqa: BLE001 (the whole download can fail)
            logger.warning("Quote fallback failed (%s); using what is cached", error)
            return {}
        now = datetime.now(UTC).isoformat()
        return {
            provider_symbol: ProviderPrice(
                price=points[-1].close,
                prev_close=points[-2].close if len(points) > 1 else None,
                currency=None,
                quote_time=now,
            )
            for provider_symbol, points in closes.items()
        }

    @classmethod
    def _collect_metadata(
        cls,
        symbols: Sequence[str],
        provider_symbols: Mapping[str, str],
        known: Mapping[str, Quote],
        prices: Mapping[str, ProviderPrice],
        max_age: timedelta,
    ) -> dict[str, dict]:
        """Fetch metadata for the symbols that are stale under the slower TTL.

        Describing a symbol is a full scrape per symbol and heavily rate-limited,
        so it runs once on first sight of a symbol and roughly monthly after, or
        daily for a caller showing fundamentals.
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
            return cls.provider.describe(due)
        except Exception as error:  # noqa: BLE001 (metadata is best-effort)
            logger.debug("Could not fetch quote metadata: %s", error)
            return {}

    @staticmethod
    def clear(symbols: Iterable[str] | None = None) -> int:
        """Drop cached quotes.

        Args:
            symbols: Symbols to drop, or None to empty the cache.

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
        *,
        refresh: bool = False,
        offline: bool = False,
    ) -> dict[str, dict[PriceRange, RangeHistory]]:
        """Return each symbol's condensed price history, fetching what is due.

        History is fetched at most once a day: each range is measured from its
        anchor to the latest price, so the moves stay current between fetches.

        Args:
            symbols: Symbols to read.
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
                stored.update(cls._refetch_history(due, today))
        return {symbol: stored[symbol][1] for symbol in wanted if symbol in stored}

    @classmethod
    def intraday(
        cls,
        symbols: Sequence[str],
        *,
        offline: bool = False,
    ) -> dict[str, dict[PriceRange, RangeHistory]]:
        """Return the ranges drawn from live bars (`2h`, `1d`, `2d`, `1wk`).

        These are never stored.

        Args:
            symbols: Symbols to read.
            offline: Never touch the network, which leaves nothing to return.

        Returns:
            Each symbol the provider had bars for, mapped to its ranges.
        """
        wanted = _unique_upper(symbols)
        provider = cls.provider
        if offline or not wanted or not provider.reachable():
            return {}
        provider_symbols = {provider.spell(symbol): symbol for symbol in wanted}
        try:
            fetched = provider.intraday(list(provider_symbols))
        except Exception as error:  # noqa: BLE001 (the provider raises freely)
            logger.debug("Could not fetch intraday bars: %s", error)
            return {}
        week_from = datetime.now(TORONTO_TZ).date() - timedelta(days=7)
        condensed: dict[str, dict[PriceRange, RangeHistory]] = {}
        for provider_symbol, bars in fetched.items():
            ranges = condense_intraday(bars.minutes, bars.fives, week_from=week_from)
            if ranges:
                condensed[provider_symbols[provider_symbol]] = ranges
        return condensed

    @classmethod
    def _refetch_history(
        cls,
        symbols: Sequence[str],
        today: date,
    ) -> dict[str, tuple[datetime, dict[PriceRange, RangeHistory]]]:
        """Fetch, condense and store the history of every symbol due.

        The fetch returns only symbols it found history for, so one the
        provider returned nothing for keeps whatever it had.
        """
        provider = cls.provider
        if not provider.reachable():
            return {}
        provider_symbols = {provider.spell(symbol): symbol for symbol in symbols}
        try:
            fetched = provider.history(list(provider_symbols), today=today)
        except Exception as error:  # noqa: BLE001 (the provider raises freely)
            logger.warning(
                "Could not fetch price history (%s); using what is kept",
                error,
            )
            return {}

        now = datetime.now(UTC)
        written: dict[str, tuple[datetime, dict[PriceRange, RangeHistory]]] = {}
        for provider_symbol, bars in fetched.items():
            ranges = condense(
                bars.daily,
                bars.weekly,
                bars.monthly,
                today=today,
                hourly=bars.hourly,
            )
            if not ranges:
                continue
            symbol = provider_symbols[provider_symbol]
            _write_history(symbol, ranges, now)
            written[symbol] = (now, ranges)
        return written

    @classmethod
    def _fill_gaps(
        cls,
        symbols: Sequence[str],
        cached: Mapping[str, Quote],
    ) -> dict[str, Quote]:
        """Return a quote for every requested symbol, empty where there is none."""
        return {
            symbol: cached.get(
                symbol,
                Quote(symbol=symbol, provider_symbol=cls.provider.spell(symbol)),
            )
            for symbol in symbols
        }


def _meta_max_age(*, fundamentals: bool) -> timedelta:
    """How old cached metadata may be: hours when fundamentals are shown."""
    config = get_config()
    if fundamentals:
        return timedelta(hours=config.quotes_fundamentals_ttl_hours)
    return timedelta(days=config.quotes_metadata_ttl_days)


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
    price: ProviderPrice | None,
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
        price=price.price,
        prev_close=price.prev_close,
        currency=_currency(price.currency) or base.currency,
        quote_time=price.quote_time,
        fetched_at=now,
        status=QuoteStatus.OK,
    )
    if meta is not None:
        updated = replace(
            updated,
            name=read_text(meta.get("name")) or updated.name,
            sector=read_text(meta.get("sector")) or updated.sector,
            exchange=read_text(meta.get("exchange")) or updated.exchange,
            market_cap=read_number(meta.get("market_cap")) or updated.market_cap,
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


def _fundamentals(values: Mapping[Any, Any]) -> Fundamentals:
    """Read fundamentals out of a mapping keyed by their field names."""
    read: dict[str, Any] = {}
    for item in fields(Fundamentals):
        raw = values.get(item.name)
        numeric = column_of(item).kind is Kind.NUMBER
        read[item.name] = read_number(raw) if numeric else read_text(raw)
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
