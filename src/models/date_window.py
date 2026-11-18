"""A run of days, both ends included."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date


@dataclass(frozen=True)
class DateWindow:
    """A run of days from `start` to `end`, both inclusive."""

    start: date
    end: date
