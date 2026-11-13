"""What changed in each pool since the latest update, and saving the next baseline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from db import update_baselines
from domain import TORONTO_TZ, Currency
from domain.numeric import ZERO
from engine.accounts import Pool
from engine.pools import cad_total
from models import UpdateBaseline

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from decimal import Decimal

    from engine.pools import FolioValuation


@dataclass(frozen=True)
class SinceUpdate:
    """One pool's change since the latest update, in CAD.

    Attributes:
        pool: The pool that changed.
        saved_at: When the latest update or import saved the baseline.
        growth: Market value change less the net deposits added, or None when
            either side could not be priced.
        net_deposits: Net deposits added since.
        dividends: Dividends paid since.
        new_txns: Transactions added since.
    """

    pool: Pool
    saved_at: datetime
    growth: Decimal | None
    net_deposits: Decimal
    dividends: Decimal
    new_txns: int

    @property
    def saved_on(self) -> str:
        """The day the baseline was saved, in Toronto, `YYYY-MM-DD`."""
        return self.saved_at.astimezone(TORONTO_TZ).date().isoformat()


def since_last_update(
    baselines: Sequence[UpdateBaseline],
    now: UpdateBaseline,
    usd_cad: Decimal,
) -> SinceUpdate | None:
    """Compare one pool's totals now against the latest baseline.

    Flows are compared in the currency they moved in, and only the change is
    converted, at today's rate, so old USD dividends are never revalued. A pool
    the baseline lacks opened since, and counts from zero.

    Args:
        baselines: Every pool's baseline, empty before the first is saved.
        now: The pool's totals now.
        usd_cad: Today's CAD per USD.

    Returns:
        The change, or None before the first baseline.
    """
    if not baselines:
        return None
    then = next(
        (b for b in baselines if (b.grain, b.pool) == (now.grain, now.pool)),
        _empty(now, baselines[0].saved_at),
    )
    deposits = cad_total(_change(then.net_deposits, now.net_deposits), usd_cad)
    dividends = cad_total(_change(then.dividends, now.dividends), usd_cad)
    growth = None
    if then.market_value is not None and now.market_value is not None:
        growth = now.market_value - then.market_value - deposits
    return SinceUpdate(
        pool=Pool(now.grain, now.pool),
        saved_at=then.saved_at,
        growth=growth,
        net_deposits=deposits,
        dividends=dividends,
        new_txns=now.txn_count - then.txn_count,
    )


def _empty(pool: UpdateBaseline, saved_at: datetime) -> UpdateBaseline:
    """Stand in for a pool that held nothing when the baseline was saved."""
    return UpdateBaseline(
        grain=pool.grain,
        pool=pool.pool,
        saved_at=saved_at,
        market_value=ZERO,
        net_deposits_cad=ZERO,
        net_deposits_usd=ZERO,
        dividends_cad=ZERO,
        dividends_usd=ZERO,
        txn_count=0,
    )


def _change(
    then: Mapping[Currency, Decimal],
    now: Mapping[Currency, Decimal],
) -> dict[Currency, Decimal]:
    """Subtract one set of per-currency amounts from another."""
    return {currency: now[currency] - then[currency] for currency in now}


def record_update(
    valuation: FolioValuation,
    pools: Sequence[Pool],
) -> list[SinceUpdate] | None:
    """Total every pool once, say what changed in some, then save the totals.

    Args:
        valuation: The folio, priced, as the update or import left it.
        pools: The pools to report the change for.

    Returns:
        Each named pool's change since the previous baseline, or None when
        this is the first.
    """
    previous = update_baselines.read()
    totals = valuation.every_pool_totals(datetime.now(TORONTO_TZ))
    update_baselines.replace(totals)
    if not previous:
        return None
    by_pool = {Pool(total.grain, total.pool): total for total in totals}
    changes = (
        since_last_update(previous, by_pool[pool], valuation.usd_cad)
        for pool in pools
        if pool in by_pool
    )
    return [change for change in changes if change is not None]


def since_update(valuation: FolioValuation, pool: Pool) -> SinceUpdate | None:
    """Say what changed in one pool since the latest update, None before the first."""
    now = valuation.pool_totals(pool, datetime.now(TORONTO_TZ))
    return since_last_update(update_baselines.read(), now, valuation.usd_cad)
