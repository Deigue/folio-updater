"""Tests for `folio ticker`: which requests it serves and which it refuses."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cli.main import app

from .helpers.cli import (
    assert_cli_success,
    assert_in_output,
    assert_not_in_output,
    run_cli_with_config,
)
from .helpers.seed import seed_fx, seed_transaction

if TYPE_CHECKING:
    from pathlib import Path

    from .test_types import TempContext

FX = {f"2025-08-{day}": "1.25" for day in range(14, 21)}


def _seed() -> None:
    """Hold one USD symbol in a taxable account and a TFSA."""
    seed_fx(FX)
    seed_transaction(account="WS-PERSONAL", amount="-1000", units="10")
    seed_transaction(account="WS-TFSA", amount="-1500", units="10")


# What each request must show, and must not, to prove it built the right view.
# Book values are converted at the seeded rates, so they never drift.
_SERVED = [
    # One symbol: a row per account type, and the pooled total of both.
    pytest.param(["TESTTKR"], ["TESTTKR held", "POOLED", "2,500.00"], [], id="one"),
    # Broker accounts instead of types, in both currencies.
    pytest.param(
        ["TESTTKR", "-b", "-c", "both"],
        ["WS-PERSONAL", "WS-TFSA", "2,500.00", "3,125.00"],
        [],
        id="by-account-both",
    ),
    pytest.param(["TESTTKR", "-c", "CAD"], ["3,125.00"], ["2,500.00"], id="cad"),
    # Priced by the provider, but never traded: no holdings to show.
    pytest.param(["OTHER"], ["Other Corp"], ["held"], id="never-traded"),
    # A comparison of every holding: only what is held.
    pytest.param([], ["TESTTKR"], ["OTHER"], id="compare-holdings"),
    # Narrowed to the TFSA, which holds half the folio's TESTTKR.
    pytest.param(["-t", "tfsa", "-s", "1wk", "-r"], ["50.00%"], [], id="compare-tfsa"),
    # Named symbols, one of which nobody knows and is left out.
    pytest.param(
        ["TESTTKR", "OTHER", "UNKNOWNSYM", "-s", "market"],
        ["Nothing known about: UNKNOWNSYM", "OTHER"],
        [],
        id="compare-named",
    ),
]


@pytest.mark.parametrize(("args", "shown", "hidden"), _SERVED)
def test_ticker_serves_one_symbol_or_a_comparison(
    temp_ctx: TempContext,
    args: list[str],
    shown: list[str],
    hidden: list[str],
) -> None:
    with temp_ctx() as ctx:
        _seed()

        result = run_cli_with_config(ctx.config, app, ["ticker", *args])

    assert_cli_success(result)
    for text in shown:
        assert_in_output(text, result)
    for text in hidden:
        assert_not_in_output(text, result)


@pytest.mark.parametrize(
    ("args", "reason"),
    [
        (["UNKNOWNSYM"], "Yahoo Finance does not know 'UNKNOWNSYM'"),
        (["UNKNOWNSYM", "--offline"], "Nothing is cached for 'UNKNOWNSYM'"),
        (["TESTTKR", "-c", "EUR"], "Unknown currency 'EUR'"),
        # A comparison of nothing anyone knows has nothing to show.
        (["UNKNOWNSYM", "NOSUCHSYM"], "Nothing known about: UNKNOWNSYM, NOSUCHSYM"),
        # Flags for the other shape of the command.
        (["TESTTKR", "-t", "tfsa"], "arrange a comparison"),
        (["TESTTKR", "OTHER", "-b"], "apply to a single symbol"),
        (["-s", "volume"], "Cannot sort by 'volume'"),
    ],
)
def test_ticker_refuses_what_it_cannot_show(
    temp_ctx: TempContext,
    args: list[str],
    reason: str,
) -> None:
    with temp_ctx() as ctx:
        _seed()

        result = run_cli_with_config(ctx.config, app, ["ticker", *args])

    assert result.exit_code == 1
    assert_in_output(reason, result)


def test_comparing_an_empty_folio_says_there_is_nothing_held(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx() as ctx:
        result = run_cli_with_config(ctx.config, app, ["ticker"])

    assert_cli_success(result)
    assert_in_output("No open positions in Portfolio to compare", result)


def test_ticker_exports_either_view(temp_ctx: TempContext, tmp_path: Path) -> None:
    with temp_ctx() as ctx:
        _seed()

        def export(*args: str) -> tuple[int, bool]:
            target = tmp_path / args[-1]
            result = run_cli_with_config(
                ctx.config,
                app,
                ["ticker", *args[:-1], "-e", str(target)],
            )
            return result.exit_code, target.exists()

        assert export("TESTTKR", "one.xlsx") == (0, True)
        assert export("all.csv") == (0, True)
        # One security's sheet holds several tables, which no CSV can.
        assert export("TESTTKR", "one.csv") == (1, False)
