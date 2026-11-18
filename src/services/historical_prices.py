"""Historical prices from the quote provider, stored in the folio database.

Each symbol's coverage records the days already asked for, so a fetch asks only
for what is missing: the days before it when an older trade appears, or the
sessions finished since. Stored prices never change.
"""

from __future__ import annotations

import logging
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING

from db import historical_prices
from domain.dates import weekday_on_or_before
from models import HistoricalPrice, HistoricalPriceCoverage
from services.quote_service import QuoteService

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from decimal import Decimal

    from models import DateWindow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoricalPriceUpdate:
    """What one top-up of historical prices did.

    Attributes:
        fetched: Symbols asked of the provider.
        stored: New prices stored.
        no_data: Symbols the provider had no prices for, now covered anyway.
        throttled: Symbols refused on a rate limit, asked again next time.
        failed: Symbols whose request failed any other way, asked again next time.
    """

    fetched: int = 0
    stored: int = 0
    no_data: frozenset[str] = frozenset()
    throttled: frozenset[str] = frozenset()
    failed: frozenset[str] = frozenset()

    @property
    def missed(self) -> frozenset[str]:
        """The symbols refused on a rate limit or failed, asked again next time."""
        return self.throttled | self.failed

    def merged(self, other: HistoricalPriceUpdate) -> HistoricalPriceUpdate:
        """Add another top-up's counts and symbols to this one's."""
        return HistoricalPriceUpdate(
            fetched=self.fetched + other.fetched,
            stored=self.stored + other.stored,
            no_data=self.no_data | other.no_data,
            throttled=self.throttled | other.throttled,
            failed=self.failed | other.failed,
        )


class HistoricalPriceService:
    """Fetch, store and read historical prices."""

    @staticmethod
    def ensure_coverage(
        spans: Mapping[str, DateWindow],
        *,
        today: date,
    ) -> HistoricalPriceUpdate:
        """Fetch the days each symbol's span needs that are not stored yet.

        Today is still trading, so a span reaching it is covered once the last
        finished session is.

        Args:
            spans: Each symbol, as the security carries it today, mapped to the
                days it needs prices for.
            today: The day the market may still be trading.
        """
        held = historical_prices.coverage(list(spans))
        priced = historical_prices.priced(list(held))
        starts = {
            symbol: start
            for symbol, span in spans.items()
            if (
                start := _first_missing(
                    span,
                    held.get(symbol),
                    priced=symbol in priced,
                    today=today,
                )
            )
            is not None
        }
        provider = QuoteService.provider
        if not starts or not provider.reachable():
            return HistoricalPriceUpdate()

        spelled = {provider.spell(symbol): symbol for symbol in starts}
        fetch = provider.historical_prices(
            {
                provider_symbol: starts[symbol]
                for provider_symbol, symbol in spelled.items()
            },
            today=today,
        )
        refused = fetch.missed
        prices = [
            HistoricalPrice(
                symbol=spelled[provider_symbol],
                date=point.when,
                price=point.close,
            )
            for provider_symbol, points in fetch.prices.items()
            for point in points
        ]
        through = (today - timedelta(days=1)).isoformat()
        covered = [
            _extended(held.get(symbol), symbol, starts[symbol], through)
            for provider_symbol, symbol in spelled.items()
            if provider_symbol not in refused
        ]
        stored = historical_prices.write(prices, covered) if covered else 0
        logger.debug(
            "Stored %d historical price(s) for %d symbol(s)",
            stored,
            len(covered),
        )
        return HistoricalPriceUpdate(
            fetched=len(spelled),
            stored=stored,
            no_data=frozenset(
                symbol
                for provider_symbol, symbol in spelled.items()
                if provider_symbol not in fetch.prices
                and provider_symbol not in refused
            ),
            throttled=frozenset(spelled[s] for s in fetch.throttled),
            failed=frozenset(spelled[s] for s in fetch.failed),
        )

    @staticmethod
    def stored(symbols: Sequence[str]) -> dict[str, StoredPrices]:
        """Read each symbol's stored prices once, to price it on any number of days."""
        spans = historical_prices.coverage(list(symbols))
        found: dict[str, StoredPrices] = {}
        for symbol in symbols:
            prices = historical_prices.read(symbol) if symbol in spans else []
            found[symbol] = StoredPrices(
                coverage=spans.get(symbol),
                dates=tuple(price.date for price in prices),
                prices=tuple(price.price for price in prices),
            )
        return found


@dataclass(frozen=True)
class StoredPrices:
    """One symbol's stored prices and the days fetched, looked up by bisection.

    Attributes:
        coverage: The days fetched, or None when the symbol never was.
        dates: Each stored trading day, ascending.
        prices: The close on each of those days, positionally aligned.
    """

    coverage: HistoricalPriceCoverage | None
    dates: tuple[str, ...]
    prices: tuple[Decimal, ...]

    def on(self, day: date) -> Decimal | None:
        """Price the symbol on a day: the latest close on or before it.

        Returns:
            None when the day is outside the symbol's coverage, or no close in
            it reaches back that far.
        """
        when = day.isoformat()
        span = self.coverage
        if span is None or not span.fetched_from <= when <= span.fetched_through:
            return None
        index = bisect_right(self.dates, when) - 1
        return self.prices[index] if index >= 0 else None


def _first_missing(
    span: DateWindow,
    held: HistoricalPriceCoverage | None,
    *,
    priced: bool,
    today: date,
) -> date | None:
    """Find the first day a span still needs, or None when it is covered.

    A span starting before the coverage refetches from its start: one request
    returns every day through today, and days already held keep their prices.
    A span starting today has no finished session yet, and a symbol the
    provider has never priced is not asked again for the sessions since.
    """
    if span.start >= today:
        return None
    if held is None or span.start.isoformat() < held.fetched_from:
        return span.start
    needed = min(span.end, weekday_on_or_before(today - timedelta(days=1)))
    through = date.fromisoformat(held.fetched_through)
    return through + timedelta(days=1) if priced and through < needed else None


def _extended(
    held: HistoricalPriceCoverage | None,
    symbol: str,
    start: date,
    through: str,
) -> HistoricalPriceCoverage:
    """Widen a symbol's coverage to take in a fetch from `start` through `through`."""
    fetched_from = start.isoformat()
    if held is not None:
        fetched_from = min(fetched_from, held.fetched_from)
    return HistoricalPriceCoverage(
        symbol=symbol,
        fetched_from=fetched_from,
        fetched_through=through,
    )
