"""Tests for ticker rename resolution."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from cli.main import app as cli_app
from cli.selection import get_ticker_family
from db import aliases
from models import TickerAlias
from services.symbols import (
    SymbolResolver,
    load_symbol_resolver,
    normalize_canadian_ticker,
)

from .helpers.cli import assert_cli_success, run_cli_with_config
from .helpers.seed import seed_transaction

if TYPE_CHECKING:
    from app import AppContext

    from .helpers.context import TempContext


def _rename(old: str, new: str, effective: str) -> TickerAlias:
    return TickerAlias(old_ticker=old, new_ticker=new, effective_date=effective)


SIMPLE = [_rename("OLDCO", "NEWCO", "2025-06-16")]
CHAIN = [_rename("AAA", "BBB", "2023-01-01"), _rename("BBB", "CCC", "2024-01-01")]


def test_canonical_follows_a_rename() -> None:
    assert SymbolResolver(SIMPLE).canonical("OLDCO") == "NEWCO"


def test_canonical_leaves_an_unrenamed_symbol_alone() -> None:
    assert SymbolResolver(SIMPLE).canonical("MSFT") == "MSFT"


def test_canonical_upper_cases() -> None:
    assert SymbolResolver(SIMPLE).canonical(" oldco ") == "NEWCO"


def test_a_rename_stored_in_lower_case_still_resolves() -> None:
    resolver = SymbolResolver([_rename("oldco", "newco", "2025-06-16")])

    assert resolver.canonical("OLDCO") == "NEWCO"
    assert resolver.family("NEWCO") == ["NEWCO", "OLDCO"]


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
        [_rename("AAA", "BBB", "2023-01-01"), _rename("BBB", "AAA", "2024-01-01")],
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
def test_provider_symbol(ticker: str, expected: str) -> None:
    assert SymbolResolver([]).provider_symbol(ticker) == expected


def test_a_configured_override_beats_the_spelling_rule() -> None:
    resolver = SymbolResolver([], {"VNTKR.TO": "VNTKR.V"})

    assert resolver.provider_symbol("VNTKR.TO") == "VNTKR.V"
    # Everything else still follows the rule.
    assert resolver.provider_symbol("REI.UN.TO") == "REI-UN.TO"


def test_an_override_is_matched_regardless_of_case() -> None:
    resolver = SymbolResolver([], {"vntkr.to": "VNTKR.V"})

    assert resolver.provider_symbol("VNTKR.TO") == "VNTKR.V"


def test_an_override_applies_after_a_rename() -> None:
    resolver = SymbolResolver(SIMPLE, {"NEWCO": "NEWCO.OVERRIDE"})

    assert resolver.provider_symbol("OLDCO") == "NEWCO.OVERRIDE"


def test_load_symbol_resolver_without_an_alias_table(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_transaction(ticker="MSFT")
        assert load_symbol_resolver().canonical("MSFT") == "MSFT"


def test_selection_delegates_to_the_resolver(temp_ctx: TempContext) -> None:
    """A query for either name finds the stored alias's whole family."""
    with temp_ctx():
        seed_transaction(ticker="OLDCO")
        aliases.write(SIMPLE)

        assert sorted(get_ticker_family("NEWCO")) == ["NEWCO", "OLDCO"]


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


# -- ALIAS AUDIT TRAIL --------------------------------------------------------

FOOTER = ["=" * 80, ""]


def _symbol(ctx: AppContext, *args: str) -> None:
    assert_cli_success(run_cli_with_config(ctx.config, cli_app, ["symbol", *args]))


def _entries(lines: list[str]) -> list[list[str]]:
    """Split the audit trail into its entries, footers dropped."""
    entries, current = [], []
    for line in lines:
        if line == FOOTER[0]:
            entries.append(current)
            current = []
        elif line:
            current.append(line)
    return entries


def test_each_alias_change_is_one_entry_naming_its_backup(
    temp_ctx: TempContext,
    audit_trail: list[str],
) -> None:
    """Added, replaced, re-added as it stands, removed, then removed again."""
    with temp_ctx(backup={"enabled": True}) as ctx:
        seed_transaction(ticker="OLDCO")
        _symbol(ctx, "--add", "oldco", "newco", "2025-06-16")
        _symbol(ctx, "--add", "OLDCO", "OTHERCO", "2025-07-02")
        _symbol(ctx, "--add", "OLDCO", "OTHERCO", "2025-07-02")
        _symbol(ctx, "--delete", "oldco")
        _symbol(ctx, "--delete", "OLDCO")

    added, replaced, unchanged, removed, missing = _entries(audit_trail)
    assert added[0] == "ADD ALIAS OLDCO (folio symbol)"
    assert added[1].startswith("  STORE 1 alias; backup folio_")
    assert added[2:] == ["    + OLDCO -> NEWCO from 2025-06-16", "DONE 1 alias added"]
    assert replaced[1].startswith("  REPLACE 1 alias; backup folio_")
    assert replaced[2:] == [
        (
            "    ~ OLDCO: NewTicker NEWCO -> OTHERCO;"
            " EffectiveDate 2025-06-16 -> 2025-07-02"
        ),
        "DONE 1 alias replaced",
    ]
    assert unchanged[1:] == [
        "  KEEP 1 alias, already as given",
        "    = OLDCO -> OTHERCO from 2025-07-02",
        "DONE alias unchanged",
    ]
    assert removed[0] == "REMOVE ALIAS OLDCO (folio symbol)"
    assert removed[1].startswith("  REMOVE 1 alias; backup folio_")
    assert removed[2:] == [
        "    - OLDCO -> OTHERCO from 2025-07-02",
        "DONE 1 alias removed",
    ]
    assert missing == [
        "REMOVE ALIAS OLDCO (folio symbol)",
        "DONE nothing removed: no alias for OLDCO",
    ]


def test_an_alias_the_table_refuses_closes_failed(
    temp_ctx: TempContext,
    audit_trail: list[str],
) -> None:
    with (
        temp_ctx() as ctx,
        patch.object(
            aliases,
            "write",
            side_effect=sqlite3.IntegrityError("CHECK constraint failed"),
        ),
    ):
        result = run_cli_with_config(
            ctx.config,
            cli_app,
            ["symbol", "--add", "OLDCO", "NEWCO", "2025-06-16"],
        )

    assert isinstance(result.exception, sqlite3.IntegrityError)
    assert audit_trail[1:] == ["FAILED: CHECK constraint failed", *FOOTER]
