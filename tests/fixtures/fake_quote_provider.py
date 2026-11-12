"""A quote provider that answers from what a test hands it, and records requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from services.quote_provider import (
    HistoryBars,
    IntradayBars,
    PriceFetch,
    ProviderPrice,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from services.price_history import PricePoint


def provider_price(
    price: float | str,
    prev_close: float | str | None = None,
    currency: str | None = "USD",
) -> ProviderPrice:
    """Build a provider price, read just now.

    Returns:
        The price, with exact decimals for whatever was given.
    """
    return ProviderPrice(
        price=Decimal(str(price)),
        prev_close=None if prev_close is None else Decimal(str(prev_close)),
        currency=currency,
        quote_time=datetime.now(UTC).isoformat(),
    )


@dataclass
class FakeQuoteProvider:
    """Stands in for the quote provider. It spells each symbol as itself.

    Attributes:
        prices_known: What `prices` answers for each symbol it knows.
        throttled: Symbols a rate limit refuses.
        failed: Symbols whose request fails any other way.
        closes: What the daily-close fallback answers.
        info: What `describe` answers.
        bars: What `history` answers.
        live: What `intraday` answers.
        up: Whether the provider can be reached.
        calls: Each request made, as (method, symbols asked for).
    """

    name: str = "fake"
    prices_known: dict[str, ProviderPrice] = field(default_factory=dict)
    throttled: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)
    closes: dict[str, list[PricePoint]] = field(default_factory=dict)
    info: dict[str, dict] = field(default_factory=dict)
    bars: dict[str, HistoryBars] = field(default_factory=dict)
    live: dict[str, IntradayBars] = field(default_factory=dict)
    up: bool = True
    calls: list[tuple[str, list[str]]] = field(default_factory=list)

    def asked(self, method: str) -> list[list[str]]:
        """Every batch one method was asked for, in order.

        Returns:
            The symbols of each call to `method`.
        """
        return [symbols for name, symbols in self.calls if name == method]

    def reachable(self) -> bool:
        """Answer whether the provider is up."""
        return self.up

    def spell(self, symbol: str) -> str:
        """Spell every symbol as itself."""
        return symbol

    def prices(self, provider_symbols: Sequence[str]) -> PriceFetch:
        """Price what it knows, and refuse the throttled and failed symbols."""
        asked = list(provider_symbols)
        self.calls.append(("prices", asked))
        return PriceFetch(
            prices={s: self.prices_known[s] for s in asked if s in self.prices_known},
            throttled=frozenset(s for s in asked if s in self.throttled),
            failed=frozenset(s for s in asked if s in self.failed),
        )

    def daily_closes(
        self,
        provider_symbols: Sequence[str],
    ) -> dict[str, list[PricePoint]]:
        """Answer from `closes`."""
        asked = list(provider_symbols)
        self.calls.append(("daily_closes", asked))
        return {s: self.closes[s] for s in asked if s in self.closes}

    def describe(self, provider_symbols: Sequence[str]) -> dict[str, dict]:
        """Answer from `info`."""
        asked = list(provider_symbols)
        self.calls.append(("describe", asked))
        return {s: self.info[s] for s in asked if s in self.info}

    def history(
        self,
        provider_symbols: Sequence[str],
        *,
        today: date,  # noqa: ARG002 (the protocol's shape)
    ) -> dict[str, HistoryBars]:
        """Answer from `bars`."""
        asked = list(provider_symbols)
        self.calls.append(("history", asked))
        return {s: self.bars[s] for s in asked if s in self.bars}

    def intraday(self, provider_symbols: Sequence[str]) -> dict[str, IntradayBars]:
        """Answer from `live`."""
        asked = list(provider_symbols)
        self.calls.append(("intraday", asked))
        return {s: self.live[s] for s in asked if s in self.live}
