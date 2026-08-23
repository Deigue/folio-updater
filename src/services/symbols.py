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

from app import get_config
from db.queries import get_alias_edges, get_connection
from term import announce

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

logger = logging.getLogger(__name__)

# Exchange suffixes Yahoo already spells with a dot. Anything else after a dot
# is a share class, which Yahoo spells with a dash, so this has to be an
# explicit list rather than a pattern: `BRK.B` and `PMN.V` look identical.
_EXCHANGE_SUFFIXES = frozenset({"TO", "V", "NE", "CN", "AQ", "L", "AX", "HK"})


class SymbolResolver:
    """Resolve tickers across renames, from one load of the alias table."""

    def __init__(
        self,
        edges: Sequence[tuple[str, str, str]],
        overrides: Mapping[str, str] | None = None,
    ) -> None:
        """Build the resolver from raw alias rows.

        Args:
            edges: `(old_ticker, new_ticker, effective_date)` triples, ordered
                by effective date so multi-hop chains resolve deterministically.
            overrides: Canonical symbol to provider symbol, for the handful the
                spelling rule cannot derive. Keys are matched case-insensitively.
        """
        self._forward: dict[str, tuple[str, str]] = {}
        self._backward: dict[str, list[str]] = {}
        for old, new, effective in edges:
            self._forward[old] = (new, effective)
            self._backward.setdefault(new, []).append(old)
        self._overrides: dict[str, str] = {
            str(key).strip().upper(): str(value)
            for key, value in (overrides or {}).items()
        }
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

    def yahoo_symbol(self, ticker: str) -> str:
        """Render a canonical symbol the way Yahoo Finance spells it.

        Yahoo separates an *exchange* with a dot and a *share class* with a
        dash. Both can appear at once, so the exchange suffix is split off first
        and only what remains is dashed: `REI.UN.TO` is the `UN` class of `REI`
        on Toronto, and Yahoo spells that `REI-UN.TO`.

        Args:
            ticker: Symbol to translate.

        Returns:
            The Yahoo form, or the configured override when one is set for this
            symbol.
        """
        symbol = self.canonical(ticker)
        override = self._overrides.get(symbol)
        if override:
            return override
        base, _, suffix = symbol.rpartition(".")
        if base and suffix in _EXCHANGE_SUFFIXES:
            return f"{base.replace('.', '-')}.{suffix}"
        return symbol.replace(".", "-")


def load_symbol_resolver() -> SymbolResolver:
    """Read the whole alias table once and build a resolver from it.

    Returns:
        A `SymbolResolver` over every rename recorded in the folio, carrying the
        configured provider-symbol overrides.
    """
    with get_connection() as conn:
        edges = get_alias_edges(conn)
    return SymbolResolver(edges, get_config().quotes_symbol_overrides)


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
