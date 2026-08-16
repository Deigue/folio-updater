"""Tests for ticker rename resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cli.selection import get_ticker_family
from db import get_connection
from services.symbols import SymbolResolver, load_symbol_resolver

from .helpers.seed import seed_transaction

if TYPE_CHECKING:
    from .test_types import TempContext


SIMPLE = [("SPLG", "SPYM", "2025-10-31")]
CHAIN = [("AAA", "BBB", "2023-01-01"), ("BBB", "CCC", "2024-01-01")]


def test_canonical_follows_a_rename() -> None:
    assert SymbolResolver(SIMPLE).canonical("SPLG") == "SPYM"


def test_canonical_leaves_an_unrenamed_symbol_alone() -> None:
    assert SymbolResolver(SIMPLE).canonical("MSFT") == "MSFT"


def test_canonical_upper_cases() -> None:
    assert SymbolResolver(SIMPLE).canonical(" splg ") == "SPYM"


def test_canonical_follows_a_multi_hop_chain() -> None:
    assert SymbolResolver(CHAIN).canonical("AAA") == "CCC"


@pytest.mark.parametrize(
    ("on", "expected"),
    [
        ("2025-10-30", "SPYM"),  # before the rename
        ("2025-10-31", "SPLG"),  # on and after the rename
        ("2026-03-01", "SPLG"),
    ],
)
def test_canonical_is_time_bounded(on: str, expected: str) -> None:
    assert SymbolResolver(SIMPLE).canonical("SPLG", on) == expected


def test_multi_hop_respects_each_edges_date() -> None:
    resolver = SymbolResolver(CHAIN)
    assert resolver.canonical("AAA", "2022-06-01") == "CCC"
    # After AAA was renamed, an "AAA" row is a different security.
    assert resolver.canonical("AAA", "2023-06-01") == "AAA"
    assert resolver.canonical("BBB", "2023-06-01") == "CCC"


def test_a_cycle_does_not_hang() -> None:
    resolver = SymbolResolver(
        [("AAA", "BBB", "2023-01-01"), ("BBB", "AAA", "2024-01-01")],
    )
    assert resolver.canonical("AAA") in {"AAA", "BBB"}


def test_family_is_not_time_bounded() -> None:
    """Query selection wants every row that ever wore either name."""
    resolver = SymbolResolver(SIMPLE)
    assert resolver.family("SPLG") == ["SPLG", "SPYM"]
    assert resolver.family("SPYM") == ["SPLG", "SPYM"]


def test_family_spans_a_whole_chain() -> None:
    assert SymbolResolver(CHAIN).family("BBB") == ["AAA", "BBB", "CCC"]


def test_family_of_an_unknown_symbol_is_itself() -> None:
    assert SymbolResolver(SIMPLE).family("MSFT") == ["MSFT"]


@pytest.mark.parametrize(
    ("ticker", "expected"),
    [
        ("MSFT", "MSFT"),
        ("RY.TO", "RY.TO"),  # already a Yahoo exchange suffix
        ("REI-UN.TO", "REI-UN.TO"),
        ("BRK.B", "BRK-B"),  # a US share class Yahoo spells with a dash
    ],
)
def test_yahoo_symbol(ticker: str, expected: str) -> None:
    assert SymbolResolver([]).yahoo_symbol(ticker) == expected


def test_load_symbol_resolver_without_an_alias_table(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_transaction(ticker="MSFT")
        assert load_symbol_resolver().canonical("MSFT") == "MSFT"


def test_selection_delegates_to_the_resolver(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_transaction(ticker="SPLG")
        with get_connection() as conn:
            assert get_ticker_family(conn, "SPLG") == ["SPLG"]
