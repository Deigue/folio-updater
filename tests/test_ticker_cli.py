"""Tests for `folio ticker`: which requests it serves and which it refuses."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cli.main import app

from .helpers.cli import assert_cli_success, run_cli_with_config
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


@pytest.mark.parametrize(
    "args",
    [
        ["TESTTKR"],
        ["TESTTKR", "-b", "-c", "both"],
        ["TESTTKR", "-c", "CAD"],
        # Priced by the provider, but never traded: no holdings to show.
        ["OTHER"],
        # Comparisons: every holding, one pool's, or the symbols named, one of
        # which nobody knows and is left out.
        [],
        ["-t", "tfsa", "-s", "1wk", "-r"],
        ["TESTTKR", "OTHER", "UNKNOWNSYM", "-s", "market"],
    ],
)
def test_ticker_serves_one_symbol_or_a_comparison(
    temp_ctx: TempContext,
    args: list[str],
) -> None:
    with temp_ctx() as ctx:
        _seed()

        assert_cli_success(run_cli_with_config(ctx.config, app, ["ticker", *args]))


@pytest.mark.parametrize(
    "args",
    [
        ["UNKNOWNSYM"],
        ["UNKNOWNSYM", "--offline"],
        ["TESTTKR", "-c", "EUR"],
        # A comparison of nothing anyone knows has nothing to show.
        ["UNKNOWNSYM", "NOSUCHSYM"],
        # Flags for the other shape of the command.
        ["TESTTKR", "-t", "tfsa"],
        ["TESTTKR", "OTHER", "-b"],
        ["-s", "volume"],
    ],
)
def test_ticker_refuses_what_it_cannot_show(
    temp_ctx: TempContext,
    args: list[str],
) -> None:
    with temp_ctx() as ctx:
        _seed()

        result = run_cli_with_config(ctx.config, app, ["ticker", *args])

        assert result.exit_code == 1


def test_comparing_an_empty_folio_says_there_is_nothing_held(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx() as ctx:
        assert_cli_success(run_cli_with_config(ctx.config, app, ["ticker"]))


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
