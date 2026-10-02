"""Tests for `folio ticker`: which requests it serves and which it refuses."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from cli.main import app

from .helpers.cli import assert_cli_success, run_cli_with_config
from .helpers.seed import seed_fx, seed_transaction

if TYPE_CHECKING:
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
    ],
)
def test_ticker_serves_a_held_or_unheld_symbol(
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
