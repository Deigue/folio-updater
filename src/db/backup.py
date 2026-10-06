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


def rolling_backup(
    file_path: Path,
    max_backups: int | None = None,
) -> None:
    """Create rolling backups of a file.

    Args:
        file_path: Path to the file to backup
        max_backups: Number of backup files to keep. If None, uses config setting.

    Raises:
        FileNotFoundError: If the source file doesn't exist
        PermissionError: If unable to create backup files
    """
    config = get_config()
    if not config.backup_enabled:
        logger.debug("Backups are disabled, skipping backup for: %s", file_path)
        return

    if not file_path.exists():
        raise FileNotFoundError

    if max_backups is None:
        max_backups = config.max_backups

    backup_dir = config.backup_path
    file_name = file_path.name
    file_stem = file_path.stem
    subdir = backup_dir / file_name.replace(".", "_")
    subdir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(TORONTO_TZ).strftime("%Y%m%d_%H%M%S")

    if file_path == config.db_path:
        txn_count = get_txn_count()
        backup_path = subdir / f"{file_stem}_{timestamp}_{txn_count}{file_path.suffix}"

        try:
            # A connection used as a context manager only commits; it is never
            # closed. On Windows that leaves both files locked until the
            # connection is collected, so close them explicitly.
            with (
                closing(sqlite3.connect(file_path)) as source,
                closing(sqlite3.connect(backup_path)) as backup,
            ):
                source.backup(backup)
                logger.debug(
                    "SQLite backup completed: %s -> %s",
                    file_path,
                    backup_path,
                )
        except sqlite3.Error:
            logger.exception("SQLite backup failed: %s", file_path)
            raise
    else:
        backup_path = subdir / f"{file_stem}_{timestamp}{file_path.suffix}"
        shutil.copy2(file_path, backup_path)
        msg = f"Backup created: {file_path} -> {backup_path}"
        logger.debug(msg)

    # Rotate backups
    backups = sorted(
        subdir.glob(f"{file_path.stem}_*{file_path.suffix}"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    for old_backup in backups[max_backups:]:
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
        rolling_backup(get_config().db_path)
