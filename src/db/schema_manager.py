"""Schema management for the database.

This module aids in initializing and updating the database schema to match
the application's requirements.
"""

import logging
import sqlite3

from db.db import get_connection
from utils.constants import TXN_ESSENTIALS, Action, Currency, Table

logger = logging.getLogger(__name__)


# def create_txns_table() -> None:
#     """Create the transactions table in the database if it doesn't already exist.

#     Returns:
#         None

#     Raises:
#         DatabaseError: If there's an issue with database connection or SQL execution
#     """
#     # All TXN_ESSENTIALS columns as TEXT, no constraints except primary key
#     columns_def = [f'"{col}" TEXT' for col in TXN_ESSENTIALS]
#     primary_keys = ", ".join(f'"{col}"' for col in TXN_ESSENTIALS)
#     sql = f"""
#     CREATE TABLE IF NOT EXISTS "{Table.TXNS.value}" (
#         {", ".join(columns_def)},
#         PRIMARY KEY ({primary_keys})
#     )
#     """
#     with get_connection() as conn:
#         try:
#             conn.execute(sql)
#         except sqlite3.DatabaseError as e:  # pragma: no cover
#             msg = f"Failed to create table '{Table.TXNS.value}': {e}"
#             logger.exception(msg)


def create_txns_table() -> None:
    """Create the transactions table in the database if it doesn't already exist.

    Returns:
        None

    Raises:
        DatabaseError: If there's an issue with database connection or SQL execution
    """
    # Define column types and constraints
    columns_def = [
        '"TxnDate" TEXT CHECK(length("TxnDate") = 10 AND "TxnDate" GLOB "[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]")',
        f'"Action" TEXT CHECK("Action" IN ({", ".join(repr(a.value) for a in Action)}))',
        '"Amount" REAL',
        f'"$" TEXT CHECK("$" IN ({", ".join(repr(c.value) for c in Currency)}))',
        '"Price" REAL',
        '"Units" REAL',
        '"Ticker" TEXT CHECK("Ticker" = UPPER("Ticker"))',
    ]
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
