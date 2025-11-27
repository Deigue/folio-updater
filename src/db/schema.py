"""Schema management for the database.

This module aids in initializing and updating the database schema to match
the application's requirements.
"""

from __future__ import annotations

import logging
import sqlite3

from db.queries import get_connection
from utils.constants import FX_COLUMN_DEFINITIONS, TXN_COLUMN_DEFINITIONS, Table

logger = logging.getLogger(__name__)


def create_txns_table() -> None:
    """Create the transactions table in the database if it doesn't already exist.

    The table uses an auto-incrementing TxnId as the PRIMARY KEY to allow
    approved duplicate transactions while maintaining application-level
    duplicate detection on TXN_ESSENTIALS.

    Returns:
        None

    Raises:
        DatabaseError: If there's an issue with database connection or SQL execution
    """
    columns_def = [col_def.to_sql() for col_def in TXN_COLUMN_DEFINITIONS]
    _create_table(Table.TXNS, columns_def)


def create_fx_table() -> None:
    """Create the FX rates table in the database if it doesn't already exist.

    The table uses Date as the PRIMARY KEY to ensure uniqueness per date.

    Returns:
        None

    Raises:
        DatabaseError: If there's an issue with database connection or SQL execution
    """
    columns_def = [col_def.to_sql() for col_def in FX_COLUMN_DEFINITIONS]
    _create_table(Table.FX, columns_def)


def _create_table(table_name: str, columns_def: list[str]) -> None:
    """Create a table with the given name and column definitions."""
    sql = f"""
    CREATE TABLE IF NOT EXISTS "{table_name}" (
        {", ".join(columns_def)}
    )
    """
    try:
        with get_connection() as conn:
            conn.execute(sql)
            logger.debug("CREATE table '%s'", table_name)
    except sqlite3.DatabaseError as e:
        msg = f"Failed to create table '{table_name}': {e}"
        logger.exception(msg)
        raise
