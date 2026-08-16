"""Resolve ticker renames to a single canonical symbol.

`TickerAliases` maps `OldTicker -> NewTicker` with an `EffectiveDate`. Resolution
is **time-bounded**: a symbol used *after* its rename date is a different
security, not an error, because ticker symbols get reused. So an edge is
followed only for rows dated before its effective date.

`family()` is deliberately not time-bounded -- it backs query selection, where
the user asking for a ticker wants every row that ever wore either name.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from db.queries import get_alias_edges, get_connection

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

logger = logging.getLogger(__name__)

# Exchange suffixes Yahoo already spells with a dot. Anything else after a dot
# is a share class, which Yahoo spells with a dash -- so this has to be an
# explicit list rather than a pattern: `BRK.B` and `PMN.V` look identical.
_EXCHANGE_SUFFIXES = frozenset({"TO", "V", "NE", "CN", "AQ", "L", "AX", "HK"})


class SymbolResolver:
    """Resolve tickers across renames, from one load of the alias table."""

    def __init__(self, edges: Sequence[tuple[str, str, str]]) -> None:
        """Build the resolver from raw alias rows.

        Args:
            edges: `(old_ticker, new_ticker, effective_date)` triples, ordered
                by effective date so multi-hop chains resolve deterministically.
        """
        self._forward: dict[str, tuple[str, str]] = {}
        self._backward: dict[str, list[str]] = {}
        for old, new, effective in edges:
            self._forward[old] = (new, effective)
            self._backward.setdefault(new, []).append(old)

    def canonical(self, ticker: str, on: str | None = None) -> str:
        """Follow renames to the name a security carries today.

        Args:
            ticker: Symbol as written on the transaction.
            on: The transaction's date, `YYYY-MM-DD`. When given, an edge is
                followed only if the row predates the rename -- a symbol used
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
                logger.warning("Cycle in ticker aliases at '%s'; stopping there", new)
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

    def yahoo_symbol(self, ticker: str) -> str:
        """Render a canonical symbol the way Yahoo Finance spells it.

        Args:
            ticker: Symbol to translate.

        Returns:
            The Yahoo form. Listings already carrying an exchange suffix
            (`RY.TO`) pass through; a US share class written with a dot
            (`BRK.B`) becomes `BRK-B`.
        """
        symbol = self.canonical(ticker)
        base, _, suffix = symbol.rpartition(".")
        if base and suffix in _EXCHANGE_SUFFIXES:
            return symbol
        return symbol.replace(".", "-")


def load_symbol_resolver() -> SymbolResolver:
    """Read the whole alias table once and build a resolver from it.

    Returns:
        A `SymbolResolver` over every rename recorded in the folio.
    """
    with get_connection() as conn:
        return SymbolResolver(get_alias_edges(conn))
