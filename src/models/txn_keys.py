"""The synthetic key that tells two transactions are the same one."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pandas as pd

from domain import TXN_ESSENTIALS
from models.txn import txns_frame

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from models.txn import Txn


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
