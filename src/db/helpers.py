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
from domain import TXN_ESSENTIALS, Column
from domain.cells import is_blank
from models import txns_frame

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from app.audit import AuditMark
    from models import Txn


def format_transaction_summary(row: pd.Series | Mapping[str, object]) -> str:
    """Format a transaction row as one line of the audit trail.

    Args:
        row: The row, keyed by column name.

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


def audit_txns(mark: AuditMark, transactions: Sequence[Txn]) -> None:
    """Write each transaction as one marked row of the audit trail, under its stage."""
    if audit.enabled(logging.INFO):
        for txn in transactions:
            audit.row(mark, format_transaction_summary(txn.to_row()))


def _is_set(value: object) -> bool:
    """Whether a field holds a value, rather than nothing or an empty cell."""
    return not is_blank(value) and value != ""


# Amounts from source files don't always reproduce bit-for-bit between exports:
# observed drift is ~1e-6/1e-7.
# 4 decimals (a hundredth of a cent) is well above that noise floor
_KEY_DECIMALS = 4


def txn_keys(txns: Sequence[Txn]) -> list[str]:
    """Generate the synthetic key of each stored transaction, in order."""
    return generate_keys(txns_frame(txns)).tolist()


def txns_with_keys(txns: Sequence[Txn], keys: Collection[str]) -> list[Txn]:
    """Pick out the stored transactions whose synthetic key is one of `keys`."""
    return [txn for txn, key in zip(txns, txn_keys(txns), strict=True) if key in keys]


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
