"""Backup utilities for folio application."""

import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.app_context import get_config
from db import db
from utils.constants import Table

logger = logging.getLogger(__name__)


def rolling_backup(
    file_path: Path,
    max_backups: int = 50,
) -> None:
    """Create rolling backups of a file.

    Args:
        file_path: Path to the file to backup
        max_backups: Number of backup files to keep (default: 50)

    Raises:
        FileNotFoundError: If the source file doesn't exist
        PermissionError: If unable to create backup files
    """
    if not file_path.exists():  # pragma: no cover
        raise FileNotFoundError

    config = get_config()
    backup_dir = config.project_root / "backups"
    file_name = file_path.name
    file_stem = file_path.stem
    subdir = backup_dir / file_name.replace(".", "_")
    subdir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    if file_path == config.db_path:
        txn_count = 0
        with db.get_connection() as conn:
            txn_count = db.get_row_count(conn, Table.TXNS.value)
        txn_suffix = f"_txn{txn_count}" if txn_count >= 0 else "_txnNA"
        backup_path = subdir / f"{file_stem}_{timestamp}{txn_suffix}{file_path.suffix}"

        try:
            with sqlite3.connect(file_path) as source, sqlite3.connect(
                backup_path,
            ) as backup:
                source.backup(backup)
                logger.debug(
                    "SQLite backup completed: %s -> %s",
                    file_path,
                    backup_path,
                )
        except sqlite3.Error:  # pragma: no cover
            logger.exception("SQLite backup failed: %s", file_path)
            raise
        finally:
            source.close()
            backup.close()
    else:  # pragma: no cover
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
    for old_backup in backups[max_backups:]:  # pragma: no cover
        logger.debug("Removing old backup: %s", old_backup)
        old_backup.unlink()
