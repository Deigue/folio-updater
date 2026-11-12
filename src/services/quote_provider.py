"""The outside service quotes come from, and the shapes it answers in."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from domain.numeric import dec

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date
    from decimal import Decimal

    from services.price_history import PricePoint


def read_text(value: object) -> str | None:
    """Read a nullable text cell."""
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text.lower() in {"nan", "none", "<na>"} else text


def read_number(value: object) -> Decimal | None:
    """Read a nullable numeric cell."""
    if read_text(value) is None:
        return None
    number = dec(value)
    # `dec` maps anything unparseable to zero, and a zero price is not a price.
    return number or None


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

    def describe(self, provider_symbols: Sequence[str]) -> dict[str, dict]:
        """Describe a batch: name, sector, fundamentals and splits, by field name."""
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
