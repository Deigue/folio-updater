"""Reading the ISO dates the folio stores as text."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

from domain.constants import TORONTO_TZ, WEEKDAYS


def today_in_toronto() -> date:
    """Return today's date in Toronto, where the brokers' days and months run."""
    return datetime.now(TORONTO_TZ).date()


def weekday_on_or_before(day: date) -> date:
    """Return `day` itself on a weekday, or the Friday before a weekend day."""
    while day.weekday() >= WEEKDAYS:
        day -= timedelta(days=1)
    return day


def iso_day(text: str) -> date | None:
    """Read a `YYYY-MM-DD` date, or None for text that is not one."""
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def month_end(year: int, month: int) -> date:
    """Return the last calendar day of a month."""
    return date(year, month, calendar.monthrange(year, month)[1])
