"""Manages table evolution in the database."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from db import db
from db.db import get_connection
from utils.constants import Table

if TYPE_CHECKING:
    import pandas as pd

logger = logging.getLogger(__name__)


def sync_txns_table_columns(txn_df: pd.DataFrame) -> list[str]:
    """Ensure the Txns table has all columns in txn_df, return final column order.

    Arguments:
        txn_df: DataFrame with transaction data to be inserted into the database.

    Returns:
        List of columns in the Txns table after synchronization

    Raises:
        sqlite3.OperationalError: If there is an error altering the table.
    """
    with get_connection() as conn:
        existing_columns = db.get_columns(conn, Table.TXNS)
        new_columns = [col for col in txn_df.columns if col not in existing_columns]

        if new_columns:
            for column in new_columns:
                db.add_column_to_table(conn, Table.TXNS, column, "TEXT")

    final_columns = existing_columns + new_columns
    logger.debug("Final ordered columns: %s", final_columns)
    return final_columns
