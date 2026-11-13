"""A broker that writes what a test hands it, fails as told, and records requests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app import get_config
from services import DownloadResult

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date
    from pathlib import Path

    from domain import BrokerId
    from services import BrokerError, PartFailure
    from services.broker import Progress


def _touch(path: Path) -> None:
    """Write an empty file."""
    path.touch()


@dataclass
class FakeBroker:
    """Stands in for any broker, monthly statements included.

    Attributes:
        id: Which broker it stands in for.
        files: Names of the source files a download writes into the imports inbox.
        write: Writes one of `files`, given its path.
        failures: Parts a download reports failed, after writing its files.
        error: What a download raises after writing its files, if anything.
        statement_error: What a monthly statement download raises, if anything.
        downloads: The window of every download asked for.
        statement_months: The month of every monthly statement download asked for.
        retries: The reference code of every retry asked for.
    """

    id: BrokerId
    files: tuple[str, ...] = ()
    write: Callable[[Path], object] = _touch
    failures: tuple[PartFailure, ...] = ()
    error: BrokerError | None = None
    statement_error: BrokerError | None = None
    downloads: list[tuple[date | None, date | None]] = field(default_factory=list)
    statement_months: list[str] = field(default_factory=list)
    retries: list[str] = field(default_factory=list)

    def sign_in(self) -> None:
        """Need no credentials."""

    def reset_credentials(self) -> None:
        """Hold no credentials to reset."""

    def download(
        self,
        start: date | None,
        end: date | None,
        progress: Progress,  # noqa: ARG002
    ) -> DownloadResult:
        """Write `files`, then raise `error` or report `failures`."""
        self.downloads.append((start, end))
        paths = tuple(get_config().imports_path / name for name in self.files)
        for path in paths:
            self.write(path)
        if self.error is not None:
            raise self.error
        return DownloadResult(files=paths, failures=self.failures)

    def retry(self, reference_code: str, progress: Progress) -> DownloadResult:  # noqa: ARG002
        """Write `files` for an earlier request."""
        self.retries.append(reference_code)
        paths = tuple(get_config().imports_path / name for name in self.files)
        for path in paths:
            self.write(path)
        return DownloadResult(files=paths)

    def monthly_statements(self, month: str, progress: Progress) -> list[Path]:  # noqa: ARG002
        """Raise `statement_error`, or find the month not published yet."""
        self.statement_months.append(month)
        if self.statement_error is not None:
            raise self.statement_error
        return []


@dataclass(frozen=True)
class FakeBrokers:
    """The fakes a test downloads from, one per broker."""

    ibkr: FakeBroker
    wealthsimple: FakeBroker
