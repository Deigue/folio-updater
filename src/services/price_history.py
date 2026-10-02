"""Condense a symbol's price history down to what each range shows.

A range needs two things: the close its move is measured from (the anchor),
and a handful of points to draw its shape.
"""

from __future__ import annotations

import calendar
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from domain import TORONTO_TZ, PriceRange

if TYPE_CHECKING:
    from collections.abc import Sequence

# Points kept to draw one range's shape, besides its anchor. Enough for a chart
# on a wide terminal; a narrower one draws fewer of them.
CHART_POINTS = 60

# The ranges read from daily, hourly, weekly and monthly closes, and stored.
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

# The ranges drawn from live intraday bars when there are any. Never stored.
INTRADAY_RANGES: tuple[PriceRange, ...] = (
    PriceRange.HOURS_2,
    PriceRange.DAY_1,
    PriceRange.DAYS_2,
    PriceRange.WEEK_1,
)

# Sessions back the `2d` anchor sits: the latest bar, the one before, then it.
_TWO_SESSIONS = 3

# A regular North American session, on the exchange's own clock.
SESSION_OPEN = time(9, 30)
SESSION_LENGTH = timedelta(hours=6, minutes=30)


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
            most `CHART_POINTS`. The latest close is always the last.
        fill: How much of the range's time axis has elapsed. Only a session
            still trading is short of 1: a `1d` at 2 PM is about 0.7 through.
    """

    range: PriceRange
    anchor: PricePoint
    points: tuple[PricePoint, ...]
    fill: Decimal = Decimal(1)


def condense(
    daily: Sequence[PricePoint],
    weekly: Sequence[PricePoint],
    monthly: Sequence[PricePoint],
    *,
    today: date,
    hourly: Sequence[PricePoint] = (),
) -> dict[PriceRange, RangeHistory]:
    """Summarise every stored range from a symbol's closes.

    Every anchor is an official daily close. Finer bars only draw a shape: a
    month of daily closes is a couple of dozen points, so `1mo` is drawn from
    hourly bars when there are any.

    Args:
        daily: Daily closes, oldest first, reaching back past the last close of
            the previous year and past a year ago.
        weekly: Weekly closes, oldest first, reaching back past five years ago.
        monthly: Monthly closes, oldest first, from the first one listed.
        today: The day the ranges are measured back from.
        hourly: Hourly closes, stamped in UTC, covering the last month.

    Returns:
        Each range the closes reach back far enough for. A range they do not
        reach, such as `5y` for a fund launched last year, is absent.
    """
    found: dict[PriceRange, RangeHistory] = {}
    if len(daily) >= _TWO_SESSIONS:
        found[PriceRange.DAYS_2] = _summary(PriceRange.DAYS_2, daily[-_TWO_SESSIONS:])

    month = _since(daily, months_back(today, 1).isoformat())
    if month:
        anchor = month[0]
        later = [point for point in hourly if session_day(point) > session_day(anchor)]
        found[PriceRange.MONTH_1] = RangeHistory(
            PriceRange.MONTH_1,
            anchor,
            sample(later or month[1:]),
        )

    starts = {
        PriceRange.WEEK_1: (daily, today - timedelta(days=7)),
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


def condense_intraday(
    minutes: Sequence[PricePoint],
    fives: Sequence[PricePoint],
    *,
    week_from: date,
) -> dict[PriceRange, RangeHistory]:
    """Summarise the ranges drawn from live bars.

    Each anchor here is only the last bar before its window: an official close
    is not in the bars, so whoever has one (the quote's previous close, a
    stored daily close) should measure from that instead.

    Args:
        minutes: One-minute bars, oldest first, stamped in UTC, covering at
            least the latest session.
        fives: Five-minute bars, oldest first, stamped in UTC, covering the last
            week of sessions.
        week_from: The day `1wk` is measured from: bars after it are drawn.

    Returns:
        `2h`: the two hours up to the latest bar. Outside market hours that is
        the end of the last session.
        `1d`: the latest session, on a fixed session axis, so a session still
        trading fills only part of it.
        `2d`: the latest two sessions, on the same kind of axis.
        `1wk`: every session after `week_from`.
        Each is absent when the bars do not cover it.
    """
    found: dict[PriceRange, RangeHistory] = {}
    if minutes:
        cutoff = datetime.fromisoformat(minutes[-1].when) - timedelta(hours=2)
        since = _since(minutes, cutoff.isoformat())
        if since:
            found[PriceRange.HOURS_2] = _summary(PriceRange.HOURS_2, since)
    if not fives:
        return found

    sessions = sorted({session_day(point) for point in fives})
    elapsed = _elapsed(fives[-1], sessions[-1])
    found[PriceRange.DAY_1] = _window(PriceRange.DAY_1, fives, sessions[-1], elapsed)
    if len(sessions) > 1:
        # Yesterday is whole, so two sessions are half done plus half of today.
        found[PriceRange.DAYS_2] = _window(
            PriceRange.DAYS_2,
            fives,
            sessions[-2],
            (1 + elapsed) / 2,
        )
    week = [day for day in sessions if day > week_from]
    if week:
        found[PriceRange.WEEK_1] = _window(PriceRange.WEEK_1, fives, week[0])
    return found


def session_day(point: PricePoint) -> date:
    """Name the trading day a close belongs to, on the exchange's own clock.

    Args:
        point: A daily close, or an intraday bar stamped in UTC.

    Returns:
        Its session's date.
    """
    if len(point.when) == len("YYYY-MM-DD"):
        return date.fromisoformat(point.when)
    return datetime.fromisoformat(point.when).astimezone(TORONTO_TZ).date()


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


def sample[T](items: Sequence[T], count: int = CHART_POINTS) -> tuple[T, ...]:
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


def _window(
    price_range: PriceRange,
    bars: Sequence[PricePoint],
    first: date,
    fill: Decimal = Decimal(1),
) -> RangeHistory:
    """Keep the bars from session `first` on, anchored on the bar before them."""
    inside = [bar for bar in bars if session_day(bar) >= first]
    before = [bar for bar in bars if session_day(bar) < first]
    return RangeHistory(
        range=price_range,
        anchor=before[-1] if before else inside[0],
        points=sample(inside),
        fill=fill,
    )


def _elapsed(latest: PricePoint, session: date) -> Decimal:
    """Measure how far through its session a bar is: 0 at the open, 1 at the close.

    A bar is stamped with its start, so the session counts as over once its
    last five-minute bar has begun.
    """
    opening = datetime.combine(session, SESSION_OPEN, TORONTO_TZ)
    through = datetime.fromisoformat(latest.when) + timedelta(minutes=5) - opening
    ratio = Decimal(through.total_seconds()) / Decimal(SESSION_LENGTH.total_seconds())
    return min(max(ratio, Decimal(0)), Decimal(1))
