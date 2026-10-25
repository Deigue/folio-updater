"""Rolling backups of the folio database and its exported workbooks."""

from __future__ import annotations

import logging
import shutil
import sqlite3
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import TYPE_CHECKING

from app import get_config
from db.helpers import txn_count as get_txn_count
from domain import TORONTO_TZ

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)

# None outside a backup scope; inside one, whether the folio is already backed up.
_scope: ContextVar[bool | None] = ContextVar("backup_scope", default=None)


def backup_file(file_path: Path, max_backups: int | None = None) -> None:
    """Copy a file into its rolling backups.

    Args:
        file_path: Path to the file to backup
        max_backups: Number of backup files to keep. If None, uses config setting.

    Raises:
        FileNotFoundError: If the source file doesn't exist
        PermissionError: If unable to create backup files
    """
    slot = _backup_slot(file_path, max_backups)
    if slot is None:
        return
    subdir, timestamp, keep = slot

    backup_path = subdir / f"{file_path.stem}_{timestamp}{file_path.suffix}"
    shutil.copy2(file_path, backup_path)
    msg = f"Backup created: {file_path} -> {backup_path}"
    logger.debug(msg)
    _rotate(subdir, file_path.stem, file_path.suffix, keep)


def backup_database(max_backups: int | None = None) -> None:
    """Copy the folio database into its rolling backups.

    Each backup is named with the number of transactions it holds.

    Args:
        max_backups: Number of backup files to keep. If None, uses config setting.

    Raises:
        FileNotFoundError: If the database doesn't exist
        sqlite3.Error: If SQLite cannot make the copy
    """
    db_path = get_config().db_path
    slot = _backup_slot(db_path, max_backups)
    if slot is None:
        return
    subdir, timestamp, keep = slot

    backup_path = (
        subdir / f"{db_path.stem}_{timestamp}_{get_txn_count()}{db_path.suffix}"
    )
    try:
        # A connection used as a context manager only commits; it is never
        # closed. On Windows that leaves both files locked until the
        # connection is collected, so close them explicitly.
        with (
            closing(sqlite3.connect(db_path)) as source,
            closing(sqlite3.connect(backup_path)) as backup,
        ):
            source.backup(backup)
            logger.debug("SQLite backup completed: %s -> %s", db_path, backup_path)
    except sqlite3.Error:
        logger.exception("SQLite backup failed: %s", db_path)
        raise
    _rotate(subdir, db_path.stem, db_path.suffix, keep)


def _backup_slot(
    file_path: Path,
    max_backups: int | None,
) -> tuple[Path, str, int] | None:
    """Work out where a backup of `file_path` goes, or None when backups are off.

    Returns:
        The file's backup folder (created), the timestamp to name this backup
        with, and how many backups to keep.
    """
    config = get_config()
    if not config.backup_enabled:
        logger.debug("Backups are disabled, skipping backup for: %s", file_path)
        return None

    if not file_path.exists():
        raise FileNotFoundError

    subdir = config.backup_path / file_path.name.replace(".", "_")
    subdir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(TORONTO_TZ).strftime("%Y%m%d_%H%M%S")
    keep = config.max_backups if max_backups is None else max_backups
    return subdir, timestamp, keep


def _rotate(subdir: Path, stem: str, suffix: str, keep: int) -> None:
    """Delete all but the newest `keep` backups of a file.

    Newest is by the timestamp in the name. A file's modified time is its
    source's (`copy2` keeps it), so a workbook restored by hand would sort as the
    oldest and be the first one deleted.
    """
    backups = sorted(
        subdir.glob(f"{stem}_*{suffix}"),
        key=lambda f: f.name,
        reverse=True,
    )
    for old_backup in backups[keep:]:
        logger.debug("Removing old backup: %s", old_backup)
        old_backup.unlink()


@contextmanager
def backup_scope() -> Iterator[None]:
    """Back the folio up at most once for everything run inside.

    One command can write several times (an update imports each file, then
    settles each statement), and a copy before every write would crowd the
    rotation with states nobody restores to. Within a scope only the first
    `backup_folio()` acts: the copy from before the first write undoes the whole
    command. Nothing is copied when nothing is written. A nested scope joins the
    one already open.
    """
    if _scope.get() is not None:
        yield
        return
    token = _scope.set(False)
    try:
        yield
    finally:
        _scope.reset(token)


def backup_folio() -> None:
    """Take a rolling backup of the folio database, if it holds anything.

    Inside a `backup_scope()`, only the first call does anything.
    """
    if _scope.get():
        logger.debug("Folio already backed up for this command")
        return
    if _scope.get() is not None:
        _scope.set(True)
    if get_txn_count() > 0:
        backup_database()
