"""How current a report's last update, replay, quotes and FX rate are."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import numpy as np

from app import get_config
from db import update_baselines
from domain.constants import TORONTO_TZ
from services.quote_service import QuoteService

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from models import Quote

# The Bank of Canada publishes once per business day, in the afternoon: a rate a
# weekend, a holiday or a morning behind is still the current one.
_FX_STALE_BUSINESS_DAYS = 3


def fx_rate_stale(rate_date: str, on: date) -> bool:
    """Whether a rate is too far behind the day it converts to still be current."""
    behind = int(np.busday_count(rate_date, on.isoformat()))
    return behind > _FX_STALE_BUSINESS_DAYS


@dataclass(frozen=True)
class Freshness:
    """How current the figures behind a report are, for a view to put into words.

    Attributes:
        replayed_at: When the ACB was replayed, or None when just now.
        quotes_at: When the oldest quote shown was fetched, or None when
            nothing was cached.
        quotes_stale: Whether any cached quote shown is past its TTL.
        fx_date: The date of the FX rate a figure shown was converted at, or
            None when nothing was converted.
        fx_stale: Whether that rate is too old to be current.
        updated_at: When the latest update or import saved the baseline, or
            None before the first.
        update_stale: Whether that was longer ago than `update.stale_days`.
    """

    replayed_at: datetime | None = None
    quotes_at: datetime | None = None
    quotes_stale: bool = False
    fx_date: str | None = None
    fx_stale: bool = False
    updated_at: datetime | None = None
    update_stale: bool = False

    @classmethod
    def of(
        cls,
        replayed_at: datetime | None,
        quotes: Mapping[str, Quote],
        fx_date: str | None = None,
    ) -> Freshness:
        """Age the last update, a replay, the quotes shown and the rate converted at."""
        quotes_at, stale = QuoteService.quote_age(quotes)
        updated_at = _last_update()
        update_stale = updated_at is not None and datetime.now(
            updated_at.tzinfo,
        ) - updated_at > timedelta(days=get_config().update_stale_days)
        aged = cls(
            replayed_at,
            quotes_at,
            # A cache holding nothing says so; calling it stale would add nothing.
            stale and quotes_at is not None,
            updated_at=updated_at,
            update_stale=update_stale,
        )
        return aged.converted_at(fx_date)

    @classmethod
    def across(cls, pools: Sequence[Freshness]) -> Freshness:
        """One freshness for pools shown together: the oldest rate any converted at.

        Every pool comes from one valuation, so only the rate can differ, and
        only in whether a pool converted anything.
        """
        dates = [pool.fx_date for pool in pools if pool.fx_date is not None]
        return pools[0].converted_at(min(dates) if dates else None)

    def converted_at(self, fx_date: str | None) -> Freshness:
        """Date the rate a figure was converted at, and judge whether it is stale."""
        today = datetime.now(TORONTO_TZ).date()
        stale = fx_date is not None and fx_rate_stale(fx_date, today)
        return replace(self, fx_date=fx_date, fx_stale=stale)


def _last_update() -> datetime | None:
    """When the latest update or import saved the baseline, None before the first.

    A folio that was never updated, the demo among them, has no baseline.
    """
    saved = [baseline.saved_at for baseline in update_baselines.read()]
    return max(saved, default=None)
