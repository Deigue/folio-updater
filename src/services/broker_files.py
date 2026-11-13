"""Downloaded broker files: naming methodology and inboxes.

A download's name carries data read back later, such as its date range or a
statement's account.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from domain import BrokerId

# Brokers as their download file names start.
IBKR = BrokerId.IBKR
WS = BrokerId.WS

SUPPORTED_SUFFIXES = (".csv", ".xlsx")

_STATEMENT_PREFIX = f"{WS}_statement"
_DATE_RANGE = re.compile(r"_(\d{4})(\d{2})(\d{2})_(\d{4})(\d{2})(\d{2})")
_STATEMENT_NAME = re.compile(
    rf"^{_STATEMENT_PREFIX}_(?P<account>.+)_\d{{6}}$",
    re.IGNORECASE,
)


def ibkr_file_name(query_name: str, from_date: str, to_date: str) -> str:
    """Name the file of one IBKR flex query, with dates as YYYYMMDD."""
    return f"{IBKR}_{query_name}_{from_date}_{to_date}.csv"


def ibkr_retry_file_name(reference_code: str) -> str:
    """Name the file of an IBKR request retried by its reference code."""
    return f"{IBKR}_ref_{reference_code}.csv"


def ws_activities_file_name(from_date: str, to_date: str) -> str:
    """Name a Wealthsimple activities file, with dates as YYYYMMDD."""
    return f"{WS}_activities_{from_date}_{to_date}.csv"


def ws_statement_file_name(account: str, month: str) -> str:
    """Name one account's Wealthsimple statement for a YYYY-MM month."""
    return f"{_STATEMENT_PREFIX}_{account}_{month.replace('-', '')}.csv"


def broker_of(name: str) -> BrokerId | None:
    """Return the broker a file name came from, or None for any other file."""
    for broker in (IBKR, WS):
        if name.startswith(f"{broker}_"):
            return broker
    return None


def date_range_of(name: str) -> tuple[str, str] | None:
    """Return the YYYY-MM-DD range a file name carries, or None without one."""
    found = _DATE_RANGE.search(name)
    if found is None:
        return None
    y1, m1, d1, y2, m2, d2 = found.groups()
    return f"{y1}-{m1}-{d1}", f"{y2}-{m2}-{d2}"


def statement_account_of(name: str) -> str | None:
    """Return the account a statement's file name carries, or None without one."""
    match = _STATEMENT_NAME.match(Path(name).stem)
    return match.group("account") if match else None


def is_supported(name: str) -> bool:
    """Report whether a file name has a suffix folio can read."""
    return Path(name).suffix.lower() in SUPPORTED_SUFFIXES


def waiting_files(folder: Path) -> list[Path]:
    """Return the readable files waiting in a folder, in name order."""
    return sorted(
        path for path in folder.iterdir() if path.is_file() and is_supported(path.name)
    )


def statements_for_month(folder: Path, month: str) -> list[Path]:
    """Return the statements waiting in a folder for a YYYY-MM month."""
    pattern = f"{_STATEMENT_PREFIX}_*_{month.replace('-', '')}.*"
    return sorted(path for path in folder.glob(pattern) if is_supported(path.name))


def archive(path: Path, archive_folder: Path) -> Path:
    """Move a file that was read into an archive.

    Args:
        path: The file that was read.
        archive_folder: The archive it belongs in.

    Returns:
        Where the file now is. A file already in the archive stays as it is.
    """
    if path.resolve().parent == archive_folder.resolve():
        return path

    destination = archive_folder / path.name
    counter = 1
    while destination.exists():
        destination = archive_folder / f"{path.stem}_{counter}{path.suffix}"
        counter += 1
    shutil.move(str(path), str(destination))
    return destination


def delete_new_files(folder: Path, before: set[Path]) -> list[str]:
    """Delete the waiting files that were not there before.

    Args:
        folder: The inbox.
        before: The files it held earlier.

    Returns:
        The names of the files deleted.
    """
    new = [path for path in waiting_files(folder) if path not in before]
    for path in new:
        path.unlink()
    return [path.name for path in new]
