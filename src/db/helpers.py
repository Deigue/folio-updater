"""Database helper utilities.

This module contains utility functions for database operations including
table management and transaction formatting.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

import pandas as pd

from app import audit
from db.queries import add_column_to_table, get_columns, get_connection, get_row_count
from domain import TXN_ESSENTIALS, Column, Table
from domain.cells import is_blank

if TYPE_CHECKING:
    from app.audit import AuditMark

logger = logging.getLogger(__name__)


def txn_count() -> int:
    """Count the transactions currently in the folio."""
    with get_connection() as conn:
        return get_row_count(conn, Table.TXNS)


def sync_txns_table_columns(txn_df: pd.DataFrame) -> list[str]:
    """Ensure the Txns table has all columns in txn_df, return final column order.

    Args:
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
    """Format a transaction row as one line of the audit trail.

    Args:
        row: A pandas Series containing transaction data.

    Returns:
        The fields as `name=value`, joined by `|`.
    """
    parts = []
    txn_id = row.get(Column.Txn.TXN_ID)
    if _is_set(txn_id):
        parts.append(f"{Column.Txn.TXN_ID}={txn_id}")
    for col in TXN_ESSENTIALS:
        value = row.get(col)
        parts.append(f"{col}={value if _is_set(value) else 'N/A'}")
    for col in (Column.Txn.FEE, Column.Txn.SETTLE_DATE):
        value = row.get(col)
        if _is_set(value):
            parts.append(f"{col}={value}")
    return "|".join(parts)


def audit_rows(
    mark: AuditMark,
    transactions: pd.DataFrame,
    *,
    level: int = logging.INFO,
) -> None:
    """Write each transaction as one marked row of the audit trail, under its stage.

    Args:
        mark: What happened to the rows.
        transactions: The rows, one audit line each.
        level: The level the stage above them was written at.
    """
    if audit.enabled(level):
        for summary in transactions.apply(format_transaction_summary, axis=1):
            audit.row(mark, summary, level=level)


def _is_set(value: object) -> bool:
    """Whether a field holds a value, rather than nothing or an empty cell."""
    return not is_blank(value) and value != ""


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
        col_series = txn_df[col].astype(object).fillna("")
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
