"""The port every broker is downloaded through, and what a download gives back."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from db import DatabaseError, txns
from domain import TORONTO_TZ
from services.price_history import months_back
from term import console_info, console_warning

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from domain import BrokerId

# Shows what a download is doing now, such as "Downloading Trades...".
type Progress = Callable[[str], None]


class BrokerFailure(StrEnum):
    """What went wrong with a broker, which decides how to recover."""

    CREDENTIALS_EXPIRED = "credentials expired"
    LOGIN_FAILED = "login failed"
    NOT_CONFIGURED = "not configured"
    BUSY = "busy"
    UNEXPECTED_RESPONSE = "unexpected response"
    NOT_SAVED = "not saved"


class BrokerError(Exception):
    """A broker could not be downloaded from, and why."""

    def __init__(self, broker: BrokerId, kind: BrokerFailure, detail: str) -> None:
        """Record the broker, the kind of failure and the broker's own words."""
        super().__init__(detail)
        self.broker = broker
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True)
class PartFailure:
    """One part of a download that failed while the others went on, such as a query."""

    part: str
    error: BrokerError


@dataclass(frozen=True)
class DownloadResult:
    """The source files one download wrote, and the parts that failed."""

    files: tuple[Path, ...] = ()
    failures: tuple[PartFailure, ...] = ()


@dataclass(frozen=True)
class DateWindow:
    """The first and last day a download asks a broker for, both inclusive."""

    start: date
    end: date


class Broker(Protocol):
    """A broker folio downloads source files from."""

    @property
    def id(self) -> BrokerId:
        """Which broker this is."""
        ...

    def sign_in(self) -> None:
        """Make sure the broker can be reached, prompting for missing credentials."""
        ...

    def reset_credentials(self) -> None:
        """Replace the stored credentials."""
        ...

    def download(
        self,
        start: date | None,
        end: date | None,
        progress: Progress,
    ) -> DownloadResult:
        """Download every source file in the window into the imports inbox.

        Args:
            start: The first day, or None to restart on the broker's overlap day.
            end: The last day, or None for today.
            progress: Shows what the download is doing now.

        Raises:
            BrokerError: When nothing could be downloaded at all.
        """
        ...


def resolve_window(
    broker: BrokerId,
    start: date | None,
    end: date | None,
) -> DateWindow:
    """Fill in the days a download leaves open.

    A missing start restarts on the broker's overlap day, so its rows arrive again
    as expected duplicates. A broker with no transactions starts on the first of
    this month, or of last month on the first.
    """
    today = datetime.now(tz=TORONTO_TZ).date()
    return DateWindow(start=start or _restart_day(broker, today), end=end or today)


def _restart_day(broker: BrokerId, today: date) -> date:
    """Return the broker's overlap day, or the fallback first of a month."""
    name = broker.value.upper()
    try:
        overlap_day = txns.latest_date_for(broker)
        if overlap_day:
            console_info(
                f"from_date: Using latest {name} transaction date: {overlap_day}",
            )
            return date.fromisoformat(overlap_day)
    except (ValueError, OSError, DatabaseError) as e:
        console_warning(f"Warning: Could not determine latest transaction date: {e}")

    fallback = today.replace(day=1)
    if fallback == today:  # pragma: no cover
        fallback = months_back(fallback, 1)
    console_info(f"from_date: Using fallback date: {fallback.isoformat()}")
    return fallback
