"""DB Helper functions."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Generator

from app.app_context import get_config


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    """Return sqlite3.Connection. Ensure parent data folder exists."""
    conn = sqlite3.connect(get_config().db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_columns(connection: sqlite3.Connection, table_name: str) -> list[str]:
    """Return list of column names for a table (in defined order)."""
    cursor = connection.execute(f"PRAGMA table_info('{table_name}')")
    return [row[1] for row in cursor.fetchall()]
