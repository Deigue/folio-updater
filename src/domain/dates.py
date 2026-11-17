"""Reading the ISO dates the folio stores as text."""

from __future__ import annotations

from datetime import date, datetime

from domain.constants import TORONTO_TZ


def today_in_toronto() -> date:
    """Return today's date in Toronto, where the brokers' days and months run."""
    return datetime.now(TORONTO_TZ).date()


def iso_day(text: str) -> date | None:
    """Read a `YYYY-MM-DD` date, or None for text that is not one."""
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
