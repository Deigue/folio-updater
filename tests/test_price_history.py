"""Tests for condensing a price series down to what each range shows."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from domain import PriceRange
from services.price_history import (
    CHART_POINTS,
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
    assert len(year.points) == CHART_POINTS
    assert year.points[-1] == daily[-1]
    assert year.anchor not in year.points


def test_a_month_is_anchored_on_a_close_and_drawn_from_hourly_bars() -> None:
    daily = _weekdays(date(2026, 8, 3), TODAY)
    hourly = [
        PricePoint(f"2026-08-{day:02d}T15:30:00+00:00", Decimal(day))
        for day in range(24, 32)
        if date(2026, 8, day).weekday() < 5
    ]

    month = condense(daily, [], [], today=TODAY, hourly=hourly)[PriceRange.MONTH_1]

    # A month back is Sunday Aug 30: the Friday close is the start, and only the
    # bars after it draw the shape.
    assert month.anchor.when == "2026-08-28"
    assert [point.when[:10] for point in month.points] == ["2026-08-31"]


def _session(day: date, bars: int) -> list[PricePoint]:
    """Five-minute bars from the 9:30 open (13:30 UTC in summer)."""
    opening = datetime(day.year, day.month, day.day, 13, 30, tzinfo=UTC)
    return [
        PricePoint((opening + timedelta(minutes=5 * n)).isoformat(), Decimal(n))
        for n in range(bars)
    ]


def test_intraday_ranges_sit_on_the_sessions_they_cover() -> None:
    monday = _session(date(2026, 9, 28), 78)
    yesterday = _session(date(2026, 9, 29), 78)
    today = _session(TODAY, 60)  # its last bar opens at 2:25 PM

    ranges = condense_intraday(
        today,
        [*monday, *yesterday, *today],
        week_from=date(2026, 9, 28),
    )

    # The day is today's session only, five hours into six and a half.
    day = ranges[PriceRange.DAY_1]
    assert day.points[0] == today[0]
    assert day.anchor == yesterday[-1]
    assert day.fill == Decimal(5 * 3600) / Decimal(6.5 * 3600)
    # Two days is yesterday and today, yesterday's half of it whole.
    two_days = ranges[PriceRange.DAYS_2]
    assert two_days.points[0] == yesterday[0]
    assert two_days.fill == (1 + day.fill) / 2
    # A week drawn from the sessions after it begins.
    assert ranges[PriceRange.WEEK_1].points[0] == yesterday[0]
    # Two hours back from the last bar.
    assert ranges[PriceRange.HOURS_2].anchor.when == "2026-09-30T16:25:00+00:00"
    assert condense_intraday([], [], week_from=TODAY) == {}
