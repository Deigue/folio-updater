"""Tests for the IBKR service."""

from __future__ import annotations

from datetime import datetime

import pytest

from services.ibkr_service import IBKRService
from utils.constants import TORONTO_TZ


@pytest.mark.parametrize(
    ("weekday_date", "expected"),
    [
        ("2025-08-18", "2025-08-15"),  # Monday -> rolls back through the weekend
        ("2025-08-14", "2025-08-13"),  # Thursday -> previous day, no rollback
    ],
)
def test_get_last_business_day(weekday_date: str, expected: str) -> None:
    """Test that weekend rollback lands on the prior Friday, weekdays don't roll."""
    reference = datetime.strptime(weekday_date, "%Y-%m-%d").replace(tzinfo=TORONTO_TZ)
    result = IBKRService._get_last_business_day(reference)  # noqa: SLF001
    assert result.strftime("%Y-%m-%d") == expected
