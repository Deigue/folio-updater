"""Reading the ISO dates the folio stores as text."""

from __future__ import annotations

from datetime import date


def iso_day(text: str) -> date | None:
    """Read a `YYYY-MM-DD` date, or None for text that is not one."""
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
