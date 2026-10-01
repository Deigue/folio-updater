"""Tests for condensing a price series down to what each range shows."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from domain import PriceRange
from services.price_history import (
    SPARK_POINTS,
    PricePoint,
    condense,
    condense_intraday,
)

# A Wednesday. A month back is Sunday 2026-08-30.
TODAY = date(2026, 9, 30)


def _weekdays(start: date, end: date) -> list[PricePoint]:
    """One close per weekday, rising by one each session."""
    points: list[PricePoint] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            points.append(PricePoint(day.isoformat(), Decimal(len(points) + 1)))
        day += timedelta(days=1)
    return points


def test_each_range_is_anchored_on_the_last_close_on_or_before_its_start() -> None:
    daily = _weekdays(date(2025, 9, 1), TODAY)

    ranges = condense(daily, [], [], today=TODAY)

    anchors = {price_range: ranges[price_range].anchor.when for price_range in ranges}
    assert anchors == {
        # Two sessions back from the latest bar.
        PriceRange.DAYS_2: "2026-09-28",
        PriceRange.WEEK_1: "2026-09-23",
        # Sunday 2026-08-30 had no close: the Friday before is the price held.
        PriceRange.MONTH_1: "2026-08-28",
        PriceRange.MONTHS_6: "2026-03-30",
        PriceRange.YTD: "2025-12-31",
        PriceRange.YEAR_1: "2025-09-30",
    }


def test_a_range_the_history_does_not_reach_is_left_out() -> None:
    # A fund listed in March 2024: no five-year anchor, but an all-time one.
    weekly = _weekdays(date(2024, 3, 4), TODAY)[::5]
    monthly = [
        PricePoint("2024-03-01", Decimal(10)),
        PricePoint("2026-09-01", Decimal(20)),
    ]

    ranges = condense([], weekly, monthly, today=TODAY)

    assert PriceRange.YEARS_5 not in ranges
    assert ranges[PriceRange.ALL].anchor == monthly[0]
    assert ranges[PriceRange.ALL].points == (monthly[1],)


def test_a_range_keeps_its_anchor_and_a_bounded_sample_ending_at_the_latest() -> None:
    daily = _weekdays(date(2025, 9, 1), TODAY)

    year = condense(daily, [], [], today=TODAY)[PriceRange.YEAR_1]

    # Two hundred-odd sessions, whatever the symbol, condense to the same few.
    assert len(year.points) == SPARK_POINTS
    assert year.points[-1] == daily[-1]
    assert year.anchor not in year.points


def test_intraday_ranges_read_the_latest_session() -> None:
    def session(day: date, bars: int) -> list[PricePoint]:
        opening = datetime(day.year, day.month, day.day, 13, 30, tzinfo=UTC)
        return [
            PricePoint((opening + timedelta(minutes=5 * n)).isoformat(), Decimal(n))
            for n in range(bars)
        ]

    yesterday = session(date(2026, 9, 29), 78)
    today = session(TODAY, 60)  # still trading at 18:25 UTC

    ranges = condense_intraday([*yesterday, *today])

    # The day is measured from yesterday's last bar, two hours from 16:25 UTC.
    assert ranges[PriceRange.DAY_1].anchor == yesterday[-1]
    assert ranges[PriceRange.HOURS_2].anchor.when == "2026-09-30T16:25:00+00:00"
    assert ranges[PriceRange.HOURS_2].points[-1] == today[-1]
    assert condense_intraday([]) == {}
