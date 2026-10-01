"""Condense a symbol's price history down to what each range shows.

A range needs two things: the close its move is measured from (the anchor),
and a handful of points to draw its shape.
"""

from __future__ import annotations

import calendar
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from domain import PriceRange

if TYPE_CHECKING:
    from collections.abc import Sequence
    from decimal import Decimal

# Points kept to draw one range's shape, besides its anchor.
SPARK_POINTS = 20

# The ranges read from daily, weekly and monthly closes, and stored.
STORED_RANGES: tuple[PriceRange, ...] = (
    PriceRange.DAYS_2,
    PriceRange.WEEK_1,
    PriceRange.MONTH_1,
    PriceRange.MONTHS_6,
    PriceRange.YTD,
    PriceRange.YEAR_1,
    PriceRange.YEARS_5,
    PriceRange.ALL,
)

# The ranges read from today's intraday bars, which are never stored.
INTRADAY_RANGES: tuple[PriceRange, ...] = (PriceRange.HOURS_2, PriceRange.DAY_1)

# Sessions back the `2d` anchor sits: the latest bar, the one before, then it.
_TWO_SESSIONS = 3


@dataclass(frozen=True)
class PricePoint:
    """One close.

    Attributes:
        when: A `YYYY-MM-DD` date, or for an intraday bar a UTC ISO timestamp.
            Either sorts correctly as text.
        close: The closing price.
    """

    when: str
    close: Decimal


@dataclass(frozen=True)
class RangeHistory:
    """What one range shows: where it started, and its shape since.

    Attributes:
        range: The range summarised.
        anchor: The close the range's move is measured from.
        points: Closes after the anchor, oldest first, evenly sampled down to at
            most `SPARK_POINTS`. The latest close is always the last.
    """

    range: PriceRange
    anchor: PricePoint
    points: tuple[PricePoint, ...]


def condense(
    daily: Sequence[PricePoint],
    weekly: Sequence[PricePoint],
    monthly: Sequence[PricePoint],
    *,
    today: date,
) -> dict[PriceRange, RangeHistory]:
    """Summarise every stored range from a symbol's closes.

    Daily closes cover up to a year, weekly closes five, and monthly closes the
    whole listing, which keeps each download small whatever the symbol's age.

    Args:
        daily: Daily closes, oldest first, reaching back past the last close of
            the previous year and past a year ago.
        weekly: Weekly closes, oldest first, reaching back past five years ago.
        monthly: Monthly closes, oldest first, from the first one listed.
        today: The day the ranges are measured back from.

    Returns:
        Each range the closes reach back far enough for. A range they do not
        reach, such as `5y` for a fund launched last year, is absent.
    """
    found: dict[PriceRange, RangeHistory] = {}
    if len(daily) >= _TWO_SESSIONS:
        found[PriceRange.DAYS_2] = _summary(PriceRange.DAYS_2, daily[-_TWO_SESSIONS:])

    starts = {
        PriceRange.WEEK_1: (daily, today - timedelta(days=7)),
        PriceRange.MONTH_1: (daily, months_back(today, 1)),
        PriceRange.MONTHS_6: (daily, months_back(today, 6)),
        PriceRange.YTD: (daily, date(today.year - 1, 12, 31)),
        PriceRange.YEAR_1: (daily, months_back(today, 12)),
        PriceRange.YEARS_5: (weekly, months_back(today, 60)),
    }
    for price_range, (series, start) in starts.items():
        since = _since(series, start.isoformat())
        if since:
            found[price_range] = _summary(price_range, since)

    if monthly:
        found[PriceRange.ALL] = _summary(PriceRange.ALL, monthly)
    return found


def condense_intraday(bars: Sequence[PricePoint]) -> dict[PriceRange, RangeHistory]:
    """Summarise the intraday ranges from a few sessions of bars.

    Args:
        bars: Intraday closes, oldest first, stamped in UTC, covering at least
            the latest session and the one before it.

    Returns:
        `1d`: the latest session, anchored on the previous session's last bar.
        `2h`: the two hours before the latest bar, anchored on the last bar at or
        before that. Outside market hours both describe the last session.
        Either is absent when the bars do not reach back far enough.
    """
    if not bars:
        return {}
    latest = bars[-1]
    session = latest.when[:10]
    found: dict[PriceRange, RangeHistory] = {}

    opening = bisect_right([bar.when for bar in bars], session) - 1
    if opening >= 0:
        found[PriceRange.DAY_1] = _summary(PriceRange.DAY_1, bars[opening:])

    cutoff = datetime.fromisoformat(latest.when) - timedelta(hours=2)
    since = _since(bars, cutoff.isoformat())
    if since:
        found[PriceRange.HOURS_2] = _summary(PriceRange.HOURS_2, since)
    return found


def months_back(day: date, months: int) -> date:
    """Step back `months` calendar months, clamping to that month's last day.

    Args:
        day: The day to count back from.
        months: How many calendar months back.

    Returns:
        The earlier day. March 31 one month back is February's last day.
    """
    total = day.year * 12 + day.month - 1 - months
    year, month = divmod(total, 12)
    last = calendar.monthrange(year, month + 1)[1]
    return date(year, month + 1, min(day.day, last))


def sample[T](items: Sequence[T], count: int = SPARK_POINTS) -> tuple[T, ...]:
    """Pick `count` items spread evenly, always keeping the first and last.

    Args:
        items: What to sample, in order.
        count: How many to keep at most.

    Returns:
        Every item when there are no more than `count`, otherwise `count` of
        them at even steps.
    """
    if len(items) <= count:
        return tuple(items)
    last = len(items) - 1
    return tuple(items[round(step * last / (count - 1))] for step in range(count))


def _since(series: Sequence[PricePoint], start: str) -> Sequence[PricePoint] | None:
    """Slice the closes from the last one on or before `start` onward.

    A range starting on a weekend or holiday is measured from the close before
    it, which is the price a holder would have had that day.

    Returns:
        The closes, anchor first, or None when the series begins after `start`.
    """
    index = bisect_right([point.when for point in series], start) - 1
    if index < 0:
        return None
    return series[index:]


def _summary(price_range: PriceRange, since: Sequence[PricePoint]) -> RangeHistory:
    """Keep a range's anchor and a sample of the closes after it."""
    return RangeHistory(
        range=price_range,
        anchor=since[0],
        points=sample(since[1:]),
    )
