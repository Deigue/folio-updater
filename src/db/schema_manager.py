"""Schema management for the database.

This module aids in initializing and updating the database schema to match
the application's requirements.
"""

import logging
import sqlite3

from db.db import get_connection
from utils.constants import TXN_COLUMN_DEFINITIONS, TXN_ESSENTIALS, Table

logger = logging.getLogger(__name__)


def create_txns_table() -> None:
    """Create the transactions table in the database if it doesn't already exist.

    Returns:
        None

    Raises:
        DatabaseError: If there's an issue with database connection or SQL execution
    """
    # Use column definitions from constants
    columns_def = [col_def.to_sql() for col_def in TXN_COLUMN_DEFINITIONS]
    primary_keys = ", ".join(f'"{col}"' for col in TXN_ESSENTIALS)
    sql = f"""
    CREATE TABLE IF NOT EXISTS "{Table.TXNS.value}" (
        {", ".join(columns_def)},
        PRIMARY KEY ({primary_keys})
    )
    """

    with get_connection() as conn:
        try:
            conn.execute(sql)
        except sqlite3.DatabaseError as e:  # pragma: no cover
            msg = f"Failed to create table '{Table.TXNS.value}': {e}"
            logger.exception(msg)
            raise
