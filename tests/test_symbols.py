"""Tests for ticker rename resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cli.selection import get_ticker_family
from db import create_ticker_aliases_table, get_connection, insert_or_replace
from domain import Column, Table
from services.symbols import (
    SymbolResolver,
    load_symbol_resolver,
    normalize_canadian_ticker,
)

from .helpers.seed import seed_transaction

if TYPE_CHECKING:
    from .helpers.context import TempContext


SIMPLE = [("OLDCO", "NEWCO", "2025-06-16")]
CHAIN = [("AAA", "BBB", "2023-01-01"), ("BBB", "CCC", "2024-01-01")]


def test_canonical_follows_a_rename() -> None:
    assert SymbolResolver(SIMPLE).canonical("OLDCO") == "NEWCO"


def test_canonical_leaves_an_unrenamed_symbol_alone() -> None:
    assert SymbolResolver(SIMPLE).canonical("MSFT") == "MSFT"


def test_canonical_upper_cases() -> None:
    assert SymbolResolver(SIMPLE).canonical(" oldco ") == "NEWCO"


def test_canonical_follows_a_multi_hop_chain() -> None:
    assert SymbolResolver(CHAIN).canonical("AAA") == "CCC"


@pytest.mark.parametrize(
    ("on", "expected"),
    [
        ("2025-06-15", "NEWCO"),  # before the rename
        ("2025-06-16", "OLDCO"),  # on and after the rename
        ("2026-03-01", "OLDCO"),
    ],
)
def test_canonical_is_time_bounded(on: str, expected: str) -> None:
    assert SymbolResolver(SIMPLE).canonical("OLDCO", on) == expected


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
    assert resolver.family("OLDCO") == ["NEWCO", "OLDCO"]
    assert resolver.family("NEWCO") == ["NEWCO", "OLDCO"]


def test_family_spans_a_whole_chain() -> None:
    assert SymbolResolver(CHAIN).family("BBB") == ["AAA", "BBB", "CCC"]


def test_family_of_an_unknown_symbol_is_itself() -> None:
    assert SymbolResolver(SIMPLE).family("MSFT") == ["MSFT"]


@pytest.mark.parametrize(
    ("ticker", "expected"),
    [
        ("MSFT", "MSFT"),
        ("RY.TO", "RY.TO"),  # already a Yahoo exchange suffix
        ("BN.TO", "BN.TO"),
        ("VNTKR.V", "VNTKR.V"),  # TSX Venture, also a dotted exchange suffix
        ("REI-UN.TO", "REI-UN.TO"),
        ("BRK.B", "BRK-B"),  # a US share class Yahoo spells with a dash
        ("REI.UN.TO", "REI-UN.TO"),
        ("DLR.U.TO", "DLR-U.TO"),
        ("CAR.UN.TO", "CAR-UN.TO"),
    ],
)
def test_yahoo_symbol(ticker: str, expected: str) -> None:
    assert SymbolResolver([]).yahoo_symbol(ticker) == expected


def test_a_configured_override_beats_the_spelling_rule() -> None:
    resolver = SymbolResolver([], {"VNTKR.TO": "VNTKR.V"})

    assert resolver.yahoo_symbol("VNTKR.TO") == "VNTKR.V"
    # Everything else still follows the rule.
    assert resolver.yahoo_symbol("REI.UN.TO") == "REI-UN.TO"


def test_an_override_is_matched_regardless_of_case() -> None:
    resolver = SymbolResolver([], {"vntkr.to": "VNTKR.V"})

    assert resolver.yahoo_symbol("VNTKR.TO") == "VNTKR.V"


def test_an_override_applies_after_a_rename() -> None:
    resolver = SymbolResolver(SIMPLE, {"NEWCO": "NEWCO.OVERRIDE"})

    assert resolver.yahoo_symbol("OLDCO") == "NEWCO.OVERRIDE"


def test_load_symbol_resolver_without_an_alias_table(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_transaction(ticker="MSFT")
        assert load_symbol_resolver().canonical("MSFT") == "MSFT"


def test_selection_delegates_to_the_resolver(temp_ctx: TempContext) -> None:
    """A query for either name finds the stored alias's whole family."""
    with temp_ctx():
        seed_transaction(ticker="OLDCO")
        create_ticker_aliases_table()
        with get_connection() as conn:
            insert_or_replace(
                conn,
                Table.TICKER_ALIASES,
                {
                    Column.Aliases.OLD_TICKER: "OLDCO",
                    Column.Aliases.NEW_TICKER: "NEWCO",
                    Column.Aliases.EFFECTIVE_DATE: "2025-06-16",
                },
            )
            assert sorted(get_ticker_family(conn, "NEWCO")) == ["NEWCO", "OLDCO"]


def test_normalize_canadian_ticker() -> None:
    """Test the normalize_canadian_ticker function.

    This function should add .TO suffix for CAD tickers if not present.
    """
    # CAD ticker without .TO suffix
    assert normalize_canadian_ticker("SHOP", "CAD") == "SHOP.TO"
    assert normalize_canadian_ticker("TDB902", "CAD") == "TDB902.TO"

    # Test CAD ticker with .TO suffix: no change
    assert normalize_canadian_ticker("SHOP.TO", "CAD") == "SHOP.TO"
    assert normalize_canadian_ticker("TDB902.TO", "CAD") == "TDB902.TO"

    # Test non-CAD currency: no change
    assert normalize_canadian_ticker("AAPL", "USD") == "AAPL"
    assert normalize_canadian_ticker("GOOGL", "USD") == "GOOGL"
