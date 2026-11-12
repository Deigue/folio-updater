"""Resolve ticker renames to a single canonical symbol.

`TickerAliases` maps `OldTicker -> NewTicker` with an `EffectiveDate`. Resolution
is **time-bounded**: a symbol used *after* its rename date is a different
security, not an error, because ticker symbols get reused. So an edge is
followed only for rows dated before its effective date.

`family()` is deliberately not time-bounded: it backs query selection, where
the user asking for a ticker wants every row that ever wore either name.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from db import aliases
from term import announce

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from models import TickerAlias

logger = logging.getLogger(__name__)


class SymbolResolver:
    """Resolve tickers across renames, from one load of the alias table."""

    def __init__(self, renames: Sequence[TickerAlias]) -> None:
        """Build the resolver from stored renames.

        Args:
            renames: The renames, ordered by effective date so multi-hop chains
                resolve deterministically. Their tickers may be in any case.
        """
        self._forward: dict[str, tuple[str, str]] = {}
        self._backward: dict[str, list[str]] = {}
        for rename in renames:
            old, new = rename.old_ticker.upper(), rename.new_ticker.upper()
            self._forward[old] = (new, rename.effective_date)
            self._backward.setdefault(new, []).append(old)
        # Track each cycle once.
        self._reported_cycles: set[str] = set()

    def canonical(self, ticker: str, on: str | None = None) -> str:
        """Follow renames to the name a security carries today.

        Args:
            ticker: Symbol as written on the transaction.
            on: The transaction's date, `YYYY-MM-DD`. When given, an edge is
                followed only if the row predates the rename: a symbol used
                after its rename date is a different security. When None,
                every edge is followed.

        Returns:
            The canonical symbol, upper-cased.
        """
        current = ticker.strip().upper()
        seen = {current}
        while True:
            edge = self._forward.get(current)
            if edge is None:
                return current
            new, effective = edge
            if on is not None and on >= effective:
                return current
            if new in seen:
                # A cycle in the alias table is bad data, not a reason to hang.
                if new not in self._reported_cycles:
                    self._reported_cycles.add(new)
                    announce.warning(
                        f"Cycle in ticker aliases at '{new}'; stopping there. "
                        "Fix it with `folio symbol`, or renames past it will not "
                        "resolve.",
                    )
                return current
            seen.add(new)
            current = new

    def family(self, ticker: str) -> list[str]:
        """Every symbol a security has ever been known by.

        Args:
            ticker: Any symbol in the chain.

        Returns:
            The transitive closure in both directions, including the input.
        """
        start = ticker.strip().upper()
        found = {start}
        pending = [start]
        while pending:
            current = pending.pop()
            neighbours: Iterable[str] = [
                *([self._forward[current][0]] if current in self._forward else []),
                *self._backward.get(current, []),
            ]
            for neighbour in neighbours:
                if neighbour not in found:
                    found.add(neighbour)
                    pending.append(neighbour)
        return sorted(found)


def load_symbol_resolver() -> SymbolResolver:
    """Read the whole alias table once and build a resolver from it.

    Returns:
        A `SymbolResolver` over every rename recorded in the folio.
    """
    return SymbolResolver(aliases.read())


def normalize_canadian_ticker(ticker: str | None, currency: str | None) -> str | None:
    """Add the `.TO` exchange suffix a CAD-denominated ticker is missing.

    Brokers report Toronto-listed holdings with a bare ticker, but the folio
    stores the exchange-qualified form so `SHOP` (NYSE) and `SHOP.TO` stay
    distinct securities.

    Args:
        ticker: The ticker symbol to normalize.
        currency: The currency code the holding is denominated in.

    Returns:
        The ticker with a `.TO` suffix when it is CAD and lacks one, unchanged
        otherwise.
    """
    if currency == "CAD" and ticker and not ticker.endswith(".TO"):
        return f"{ticker}.TO"
    return ticker
