"""Tests for how current a report's replay, quotes and FX rate are."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from db import update_baselines
from domain import Grain
from domain.numeric import ZERO
from engine.freshness import Freshness, fx_rate_stale
from models import Quote, UpdateBaseline

if TYPE_CHECKING:
    from .helpers.context import TempContext


# 2030-01-01 is a Tuesday.
@pytest.mark.parametrize(
    ("rate_date", "on", "stale"),
    [
        ("2030-01-04", date(2030, 1, 7), False),  # Friday's rate on Monday
        ("2030-01-03", date(2030, 1, 8), False),  # three business days behind
        ("2030-01-02", date(2030, 1, 8), True),  # four business days behind
    ],
    ids=["weekend", "three-days", "four-days"],
)
def test_an_fx_rate_is_stale_past_three_business_days(
    rate_date: str,
    on: date,
    *,
    stale: bool,
) -> None:
    assert fx_rate_stale(rate_date, on) is stale


def test_freshness_ages_the_oldest_quote_and_flags_a_stale_one(
    temp_ctx: TempContext,
) -> None:
    now = datetime.now(UTC)
    replayed = now - timedelta(days=3)
    fresh = Quote(symbol="SPY", provider_symbol="SPY", fetched_at=now)
    old = Quote(
        symbol="MSFT",
        provider_symbol="MSFT",
        fetched_at=now - timedelta(hours=5),
    )

    with temp_ctx(quotes={"ttl_minutes": 15}):
        freshness = Freshness.of(replayed, {"SPY": fresh, "MSFT": old})

    assert freshness == Freshness(
        replayed_at=replayed,
        quotes_at=old.fetched_at,
        quotes_stale=True,
    )


@pytest.mark.parametrize(
    ("days_ago", "stale"),
    [(2, False), (8, True)],
    ids=["within-a-week", "past-a-week"],
)
def test_freshness_ages_the_last_update_against_the_stale_setting(
    temp_ctx: TempContext,
    days_ago: int,
    *,
    stale: bool,
) -> None:
    saved_at = datetime.now(UTC) - timedelta(days=days_ago)
    baseline = UpdateBaseline(
        grain=Grain.ACCOUNT,
        pool="ACCT-ONE",
        saved_at=saved_at,
        market_value=None,
        net_deposits_cad=ZERO,
        net_deposits_usd=ZERO,
        dividends_cad=ZERO,
        dividends_usd=ZERO,
        txn_count=3,
    )

    with temp_ctx(update={"stale_days": 7}):
        update_baselines.replace([baseline])
        freshness = Freshness.of(None, {})

    assert freshness.updated_at == saved_at
    assert freshness.update_stale is stale


def test_a_folio_never_updated_has_no_update_age(temp_ctx: TempContext) -> None:
    with temp_ctx():
        freshness = Freshness.of(None, {})

    assert freshness.updated_at is None
    assert not freshness.update_stale


def test_quotes_never_fetched_are_nothing_cached_not_stale(
    temp_ctx: TempContext,
) -> None:
    never = Quote(symbol="SPY", provider_symbol="SPY")

    with temp_ctx():
        freshness = Freshness.of(None, {"SPY": never})

    assert freshness.quotes_at is None
    assert not freshness.quotes_stale


def test_a_conversion_at_an_old_rate_is_stale(temp_ctx: TempContext) -> None:
    with temp_ctx():
        freshness = Freshness.of(None, {}, fx_date="2021-03-04")

    assert freshness.fx_date == "2021-03-04"
    assert freshness.fx_stale


def test_pools_shown_together_disclose_their_oldest_rate() -> None:
    plain = Freshness(quotes_at=datetime(2030, 1, 8, tzinfo=UTC))
    pools = [
        plain,
        plain.converted_at("2021-03-05"),
        plain.converted_at("2021-03-04"),
    ]

    assert Freshness.across(pools) == plain.converted_at("2021-03-04")
