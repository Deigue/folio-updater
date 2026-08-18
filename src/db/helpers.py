"""Database helper utilities.

This module contains utility functions for database operations including
table management and transaction formatting.
"""

from __future__ import annotations

import hashlib
import logging

import pandas as pd

from app import get_config
from db.queries import add_column_to_table, get_columns, get_connection, get_row_count
from utils.constants import TXN_ESSENTIALS, Table

logger = logging.getLogger(__name__)


def txn_count() -> int:
    """Count the transactions currently in the folio."""
    with get_connection() as conn:
        return get_row_count(conn, Table.TXNS)


def backup_folio() -> None:
    """Take a rolling backup of the folio database, if it holds anything."""
    # ! Deferred Import: utils.backup imports from db, so importing rolling_backup at
    # ! module level here would create a circular import.
    from utils.backup import rolling_backup  # noqa: PLC0415

    if txn_count() > 0:
        rolling_backup(get_config().db_path)


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
        existing_columns = get_columns(conn, Table.TXNS)
        new_columns = [col for col in txn_df.columns if col not in existing_columns]

        if new_columns:
            for column in new_columns:
                add_column_to_table(conn, Table.TXNS, column, "TEXT")

    final_columns = existing_columns + new_columns
    logger.debug("Final ordered columns: %s", final_columns)
    return final_columns


def format_transaction_summary(row: pd.Series) -> str:
    """Format a transaction row into a human-readable summary.

    Args:
        row: A pandas Series containing transaction data.

    Returns:
        A formatted string summarizing the transaction.
    """
    essential_parts = []
    for col in TXN_ESSENTIALS:
        value = row.get(col, "N/A")
        if pd.isna(value):
            value = "N/A"
        essential_parts.append(f"{col}={value}")

    return "|".join(essential_parts)


# Amounts from source files don't always reproduce bit-for-bit between exports:
# observed drift is ~1e-6/1e-7.
# 4 decimals (a hundredth of a cent) is well above that noise floor
_KEY_DECIMALS = 4


def generate_keys(txn_df: pd.DataFrame) -> pd.Series:
    """Generate synthetic primary keys based on TXN_ESSENTIALS.

    Normalisation happens columnwise so the numeric coercion stays vectorised;
    the join and hash then run in plain Python. Folios are hashed a row at a
    time on every add/edit, where the frames are one to a few dozen rows and
    pandas' per-operation overhead dwarfs the actual work: staying in Python
    past the coercion is ~13x faster on those sizes for identical output.

    Args:
        txn_df: DataFrame with transaction data

    Returns:
        Series of hash strings representing synthetic primary keys
    """
    if txn_df.empty:  # pragma: no cover
        return pd.Series([], dtype=str)

    normalized_cols: list[list[str]] = []
    for col in TXN_ESSENTIALS:
        col_series = txn_df[col].fillna("")
        numeric_series = pd.to_numeric(col_series, errors="coerce")
        normalized_cols.append(
            [
                # Numeric are rounded to _KEY_DECIMALS, trailing zeros stripped
                f"{number:.{_KEY_DECIMALS}f}".rstrip("0").rstrip(".")
                # NaN != NaN coercion.
                if number == number  # noqa: PLR0124
                else str(value).strip()
                for value, number in zip(
                    col_series.to_numpy(),
                    numeric_series.to_numpy(),
                    strict=True,
                )
            ],
        )

    return pd.Series(
        [
            hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
            for parts in zip(*normalized_cols, strict=True)
        ],
        index=txn_df.index,
        dtype=str,
    )
