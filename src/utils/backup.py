"""Backup utilities for folio application."""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING

from app.app_context import get_config
from db import db
from utils.constants import TORONTO_TZ, Table

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


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
        txn_count = 0
        with db.get_connection() as conn:
            txn_count = db.get_row_count(conn, Table.TXNS)
        backup_path = subdir / f"{file_stem}_{timestamp}_{txn_count}{file_path.suffix}"

        try:
            with (
                sqlite3.connect(file_path) as source,
                sqlite3.connect(backup_path) as backup,
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
        finally:
            source.close()
            backup.close()
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
