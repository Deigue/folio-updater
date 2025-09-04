"""Manages table evolution in the database."""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

from db import db
from db.db import get_connection
from utils.constants import Table

if TYPE_CHECKING:
    import pandas as pd

logger = logging.getLogger(__name__)


def sync_txns_table_columns(txn_df: pd.DataFrame) -> list[str]:
    """Ensure the Txns table has all columns in txn_df, return final column order."""
    with get_connection() as conn:
        existing_columns = db.get_columns(conn, Table.TXNS.value)

    new_columns = [col for col in txn_df.columns if col not in existing_columns]

    if new_columns:
        for column in new_columns:
            alter_sql = f'ALTER TABLE "{Table.TXNS.value}" ADD COLUMN "{column}" TEXT'
            with get_connection() as conn:
                try:
                    conn.execute(alter_sql)
                    logger.debug(
                        "Added new column '%s' to table '%s'",
                        column,
                        Table.TXNS.value,
                    )
                except sqlite3.OperationalError:  # pragma: no cover
                    logger.exception("Could not add column '%s'", column)

    return existing_columns + new_columns
