"""The outside service quotes come from, and the shapes it answers in."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from models import Fundamentals

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date
    from decimal import Decimal

    from models import QuoteSplit
    from services.price_history import PricePoint


@dataclass(frozen=True)
class ProviderPrice:
    """One provider symbol's price.

    Attributes:
        price: The latest price, or None when the provider sent no usable one.
        prev_close: The previous regular-session close.
        currency: The currency code as the provider spells it.
        quote_time: When the price was read, ISO 8601.
    """

    price: Decimal | None
    prev_close: Decimal | None
    currency: str | None
    quote_time: str


@dataclass(frozen=True)
class ProviderDescription:
    """What the provider says about one provider symbol beyond its price.

    Attributes:
        name: The security's name.
        sector: Its sector.
        exchange: The exchange it lists on.
        market_cap: Its market capitalisation.
        currency: The currency code as the provider spells it.
        fundamentals: Its valuation, dividend and fund figures.
        splits: Its split history: None when it could not be fetched, empty
            when there are none.
    """

    name: str | None = None
    sector: str | None = None
    exchange: str | None = None
    market_cap: Decimal | None = None
    currency: str | None = None
    fundamentals: Fundamentals = field(default_factory=Fundamentals)
    splits: tuple[QuoteSplit, ...] | None = None


@dataclass(frozen=True)
class PriceFetch:
    """What one batch of price requests came back with.

    A provider symbol in none of these is one the provider does not know.

    Attributes:
        prices: Each priced provider symbol mapped to its price.
        throttled: Provider symbols that failed on a rate limit, which their
            daily closes may still price.
        failed: Provider symbols that failed any other way.
    """

    prices: dict[str, ProviderPrice] = field(default_factory=dict)
    throttled: frozenset[str] = frozenset()
    failed: frozenset[str] = frozenset()


@dataclass(frozen=True)
class HistoryBars:
    """One symbol's dated closes at each interval the stored ranges are built from."""

    daily: list[PricePoint]
    weekly: list[PricePoint]
    monthly: list[PricePoint]
    hourly: list[PricePoint]


@dataclass(frozen=True)
class IntradayBars:
    """One symbol's bars over the last week of sessions, stamped in UTC."""

    minutes: list[PricePoint]
    fives: list[PricePoint]


class QuoteProvider(Protocol):
    """Answers for market data, keyed by the provider's own spelling of each symbol.

    Every batch method returns only the symbols it found something for.
    """

    name: str

    def reachable(self) -> bool:
        """Whether the provider can be reached at all."""
        ...

    def spell(self, symbol: str) -> str:
        """Spell a folio symbol the way the provider does."""
        ...

    def prices(self, provider_symbols: Sequence[str]) -> PriceFetch:
        """Price a batch, sorting each symbol into priced, throttled or failed."""
        ...

    def daily_closes(
        self,
        provider_symbols: Sequence[str],
    ) -> dict[str, list[PricePoint]]:
        """Fetch the last few daily closes for a batch, in one request."""
        ...

    def describe(
        self,
        provider_symbols: Sequence[str],
    ) -> dict[str, ProviderDescription]:
        """Describe a batch: name, sector, fundamentals and splits."""
        ...

    def history(
        self,
        provider_symbols: Sequence[str],
        *,
        today: date,
    ) -> dict[str, HistoryBars]:
        """Fetch enough dated closes for every stored range, back from `today`."""
        ...

    def intraday(self, provider_symbols: Sequence[str]) -> dict[str, IntradayBars]:
        """Fetch the bars behind the live ranges."""
        ...
