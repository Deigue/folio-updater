"""Tests verifying forex coverage is extended at both ends."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from cli.main import app
from db import create_txns_table
from services import ForexService
from utils.constants import TORONTO_TZ, Column

from .helpers.cli import assert_cli_success, run_cli_with_config
from .helpers.seed import seed_fx, seed_transaction

if TYPE_CHECKING:
    from collections.abc import Callable
    from unittest.mock import MagicMock

    from .test_types import TempContext


@pytest.fixture
def rate_dates(cached_fx_data: Callable[[str | None], pd.DataFrame]) -> list[str]:
    """Every date the stubbed Bank of Canada response can offer, in order.

    Args:
        cached_fx_data: The session-wide synthetic FX frame.

    Returns:
        Dates in YYYY-MM-DD format, oldest first.
    """
    return list(cached_fx_data(None)[Column.FX.DATE])


def _seed_rates(dates: list[str]) -> None:
    """Store a rate for each given date, at a value no assertion depends on."""
    seed_fx(dict.fromkeys(dates, "1.35"))


def test_ensure_coverage_defaults_to_the_earliest_transaction(
    temp_ctx: TempContext,
    rate_dates: list[str],
) -> None:
    """No caller has to work out its own start bound for the ordinary case."""
    with temp_ctx():
        oldest_txn = rate_dates[-20]
        seed_transaction(date=oldest_txn, settle_date=oldest_txn)

        assert ForexService.ensure_coverage() == 20
        assert ForexService.get_earliest_fx_date_from_db() == oldest_txn


@pytest.mark.parametrize("make_table", [False, True], ids=["no_table", "empty_table"])
def test_ensure_coverage_falls_back_to_thirty_days_ago(
    temp_ctx: TempContext,
    boc_fetch: MagicMock,
    *,
    make_table: bool,
) -> None:
    """An empty folio still needs some span, or there is nothing to fetch."""
    with temp_ctx():
        if make_table:
            create_txns_table()

        ForexService.ensure_coverage()

        # Asserted on the date requested rather than on what came back: the
        # first business day on or after the cutoff is the same for a couple of
        # days either side of it, so stored rates would not catch a drift.
        cutoff = (datetime.now(TORONTO_TZ) - timedelta(days=30)).strftime("%Y-%m-%d")
        assert boc_fetch.call_args.args == (cutoff,)


def test_ensure_coverage_backfills_history_older_than_the_earliest_rate(
    temp_ctx: TempContext,
    rate_dates: list[str],
) -> None:
    """A plain top-up from MAX(Date) only extends forward; this fixes that."""
    with temp_ctx():
        _seed_rates(rate_dates[-2:])
        assert ForexService.get_earliest_fx_date_from_db() == rate_dates[-2]

        # The refetch spans the held dates too; only the new ones are written,
        # since the rest would collide on the Date primary key.
        assert ForexService.ensure_coverage(rate_dates[-20]) == 18
        assert ForexService.get_earliest_fx_date_from_db() == rate_dates[-20]

        # Asking again finds nothing left to add.
        assert ForexService.ensure_coverage(rate_dates[-20]) == 0


def test_ensure_coverage_discards_dates_it_already_holds(
    temp_ctx: TempContext,
    rate_dates: list[str],
    boc_fetch: MagicMock,
) -> None:
    """Reaching back past what the response can offer must not double-insert."""
    with temp_ctx():
        _seed_rates(rate_dates)
        earlier = (pd.to_datetime(rate_dates[0]) - pd.Timedelta(days=7)).strftime(
            "%Y-%m-%d",
        )

        # A start older than the earliest rate held always forces a refetch,
        # but here the response carries only dates already stored: nothing is
        # written, and nothing collides on the Date primary key.
        assert ForexService.ensure_coverage(earlier) == 0
        assert boc_fetch.call_args.args == (earlier,)


def test_ensure_coverage_is_a_no_op_when_the_span_is_already_held(
    temp_ctx: TempContext,
    rate_dates: list[str],
    boc_fetch: MagicMock,
) -> None:
    with temp_ctx():
        _seed_rates(rate_dates[-20:])
        assert ForexService.ensure_coverage(rate_dates[-20], rate_dates[-1]) == 0
        boc_fetch.assert_not_called()


def test_ensure_coverage_never_chases_a_future_end(
    temp_ctx: TempContext,
    rate_dates: list[str],
    boc_fetch: MagicMock,
) -> None:
    """Dont invoke API if end date is in the future."""
    with temp_ctx():
        today = ForexService.effective_today()
        _seed_rates([*rate_dates[-5:], today])
        future = (pd.to_datetime(today) + pd.Timedelta(days=2)).strftime("%Y-%m-%d")

        assert ForexService.ensure_coverage(rate_dates[-5], future) == 0
        boc_fetch.assert_not_called()


def test_ensure_coverage_tops_up_the_forward_end(
    temp_ctx: TempContext,
    rate_dates: list[str],
    boc_fetch: MagicMock,
) -> None:
    with temp_ctx():
        _seed_rates(rate_dates[-20:-10])
        assert ForexService.ensure_coverage(rate_dates[-20]) == 10

        # Picked up from the day after the newest rate held, not from `start`.
        day_after = pd.to_datetime(rate_dates[-11]) + pd.Timedelta(days=1)
        assert boc_fetch.call_args.args == (day_after.strftime("%Y-%m-%d"),)


def test_ensure_coverage_without_any_stored_rates(
    temp_ctx: TempContext,
    rate_dates: list[str],
) -> None:
    with temp_ctx():
        assert ForexService.ensure_coverage(rate_dates[-5]) == 5


def test_ensure_coverage_handles_an_empty_response(
    temp_ctx: TempContext,
    rate_dates: list[str],
    boc_fetch: MagicMock,
) -> None:
    with temp_ctx():
        _seed_rates(rate_dates[-1:])
        boc_fetch.side_effect = None
        boc_fetch.return_value = pd.DataFrame()
        assert ForexService.ensure_coverage(rate_dates[0]) == 0


def test_getfx_backfills_rates_older_than_the_ones_held(
    temp_ctx: TempContext,
    rate_dates: list[str],
    boc_fetch: MagicMock,
) -> None:
    """`folio getfx` has to reach back for newly imported history, not just forward."""
    with temp_ctx() as ctx:
        oldest_txn = rate_dates[-20]
        seed_transaction(date=oldest_txn, settle_date=oldest_txn)
        _seed_rates(rate_dates[-1:])

        result = run_cli_with_config(ctx.config, app, ["getfx"])

        # Fetched from the earliest transaction date, not from MAX(Date) + 1.
        assert boc_fetch.call_args.args == (oldest_txn,)
        assert ForexService.get_earliest_fx_date_from_db() == oldest_txn
    assert_cli_success(result)
