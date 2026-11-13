"""What changed in a pool since the latest update, from its baseline."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from domain import Grain
from engine.since_update import since_last_update
from models import UpdateBaseline

SAVED = datetime(2022, 9, 6, 21, 30, tzinfo=UTC)
NOW = datetime(2022, 9, 14, 21, 30, tzinfo=UTC)
RATE = Decimal("1.4")


def _totals(
    pool: str,
    *,
    market_value: str | None,
    deposits_cad: str = "0",
    deposits_usd: str = "0",
    dividends_cad: str = "0",
    dividends_usd: str = "0",
    txn_count: int = 0,
    saved_at: datetime = SAVED,
) -> UpdateBaseline:
    """Build one account pool's totals."""
    return UpdateBaseline(
        grain=Grain.ACCOUNT,
        pool=pool,
        saved_at=saved_at,
        market_value=None if market_value is None else Decimal(market_value),
        net_deposits_cad=Decimal(deposits_cad),
        net_deposits_usd=Decimal(deposits_usd),
        dividends_cad=Decimal(dividends_cad),
        dividends_usd=Decimal(dividends_usd),
        txn_count=txn_count,
    )


def test_growth_is_the_market_value_change_less_new_deposits() -> None:
    """Flows convert only what moved since, at today's rate."""
    then = _totals(
        "ACCT-A",
        market_value="10000",
        deposits_cad="8000",
        deposits_usd="1000",
        dividends_usd="50",
        txn_count=10,
    )
    now = _totals(
        "ACCT-A",
        market_value="12500",
        deposits_cad="9000",
        deposits_usd="1000",
        dividends_cad="5",
        dividends_usd="70",
        txn_count=13,
        saved_at=NOW,
    )

    since = since_last_update([then], now, RATE)

    assert since is not None
    assert since.saved_at == SAVED
    assert since.net_deposits == Decimal(1000)
    assert since.dividends == Decimal(33)
    assert since.growth == Decimal(1500)
    assert since.new_txns == 3


def test_a_pool_new_since_the_baseline_counts_from_zero() -> None:
    other = _totals("ACCT-A", market_value="400", txn_count=2)
    now = _totals(
        "ACCT-B",
        market_value="3000",
        deposits_cad="2500",
        txn_count=4,
        saved_at=NOW,
    )

    since = since_last_update([other], now, RATE)

    assert since is not None
    assert since.saved_at == SAVED
    assert since.growth == Decimal(500)
    assert since.net_deposits == Decimal(2500)
    assert since.new_txns == 4


def test_nothing_changed_before_the_first_baseline() -> None:
    now = _totals("ACCT-A", market_value="3000", saved_at=NOW)

    assert since_last_update([], now, RATE) is None


def test_an_unpriced_pool_has_no_growth_but_keeps_its_flows() -> None:
    then = _totals("ACCT-A", market_value=None, dividends_cad="10")
    now = _totals("ACCT-A", market_value="900", dividends_cad="25", saved_at=NOW)

    since = since_last_update([then], now, RATE)

    assert since is not None
    assert since.growth is None
    assert since.dividends == Decimal(15)
