"""The one duplicate check, and the import stage that rejects what it finds.

Rows are keyed on TXN_ESSENTIALS and checked both against each other (within the
batch) and against stored transactions. The pipeline's filter turns the answer
into rejections.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from app import audit, get_config
from app.audit import AuditMark, audit_rows
from db import txns
from models.txn_keys import generate_keys, txn_keys

if TYPE_CHECKING:
    from collections.abc import Sequence

    from models import Txn


@dataclass(frozen=True)
class Duplicates:
    """Which rows of a batch are duplicates, and the stored transactions they match.

    Attributes:
        within: Per row, whether another row of the batch matches it (every copy).
        of_stored: Per row, whether a stored transaction matches it.
        matches: The stored transactions some row matches, in stored order.
    """

    within: pd.Series
    of_stored: pd.Series
    matches: list[Txn]


def find_duplicates(rows: pd.DataFrame, stored: Sequence[Txn] = ()) -> Duplicates:
    """Find the rows matching each other or one of the `stored` transactions.

    Args:
        rows: The batch, in table columns.
        stored: The transactions to check against; leave out any the batch replaces.

    Returns:
        The duplicates found.
    """
    keys = generate_keys(rows)
    stored_keys = txn_keys(stored)
    batch_keys = set(keys)
    return Duplicates(
        within=keys.duplicated(keep=False),
        of_stored=keys.isin(set(stored_keys)),
        matches=[
            txn
            for txn, key in zip(stored, stored_keys, strict=True)
            if key in batch_keys
        ],
    )


class TransactionFilter:
    """Reject duplicate transactions."""

    @staticmethod
    def filter_db_duplicates(txn_df: pd.DataFrame) -> pd.DataFrame:
        """Reject transactions that already exist in the database.

        Args:
            txn_df: DataFrame with transaction data.

        Returns:
            DataFrame with database duplicates rejected, unless approved.
        """
        if txn_df.empty:  # pragma: no cover
            return txn_df

        stored = txns.read()
        if not stored:
            return txn_df

        audit.debug(
            "CHECK duplicates: %d stored, %d new transactions",
            len(stored),
            len(txn_df),
        )
        is_duplicate = find_duplicates(txn_df, stored).of_stored
        approved_mask, rejected_mask = TransactionFilter._process_duplicate_approval(
            txn_df,
            is_duplicate,
        )

        TransactionFilter._log_duplicates(
            txn_df,
            approved_mask,
            rejected_mask,
            duplicate_type="database",
        )

        # Return all non-duplicates plus approved duplicates
        keep_mask = ~is_duplicate | approved_mask
        return txn_df[keep_mask].copy()

    @staticmethod
    def filter_intra_import_duplicates(txn_df: pd.DataFrame) -> pd.DataFrame:
        """Reject duplicate transactions within the DataFrame itself.

        Args:
            txn_df: DataFrame with transaction data.

        Returns:
            DataFrame with duplicates rejected, unless approved.
        """
        if txn_df.empty:  # pragma: no cover
            return txn_df

        duplicate_mask = find_duplicates(txn_df).within
        if not duplicate_mask.any():
            return txn_df

        approved_mask, rejected_mask = TransactionFilter._process_duplicate_approval(
            txn_df,
            duplicate_mask,
        )

        TransactionFilter._log_duplicates(
            txn_df,
            approved_mask,
            rejected_mask,
            duplicate_type="intra-import",
        )

        # Keep non-duplicates and approved duplicates
        keep_mask = ~duplicate_mask | approved_mask
        return txn_df[keep_mask].copy()

    @staticmethod
    def remove_approval_column(txn_df: pd.DataFrame) -> pd.DataFrame:
        """Remove the duplicate approval column from the DataFrame if it exists."""
        approval_column = get_config().duplicate_approval_column
        if approval_column in txn_df.columns:
            msg = f"Duplicate approval column found: {approval_column}"
            audit.debug(msg)
            return txn_df.drop(columns=[approval_column])
        return txn_df

    @staticmethod
    def _process_duplicate_approval(
        txn_df: pd.DataFrame,
        duplicate_mask: pd.Series,
    ) -> tuple[pd.Series, pd.Series]:
        """Check which duplicates are supposed to be approved.

        Args:
            txn_df: Full transaction DataFrame
            duplicate_mask: Boolean mask indicating which rows are duplicates

        Returns:
            Tuple of (approved_duplicates_mask, rejected_duplicates_mask)
        """
        config = get_config()
        approval_column = config.duplicate_approval_column
        approval_value = config.duplicate_approval_value
        approved_mask = pd.Series([False] * len(txn_df), index=txn_df.index)
        rejected_mask = pd.Series([False] * len(txn_df), index=txn_df.index)

        if approval_column not in txn_df.columns:
            # No approval column, all duplicates are rejected
            rejected_mask.loc[txn_df[duplicate_mask].index] = True
            return approved_mask, rejected_mask

        # Check which duplicates are approved
        duplicate_indices = txn_df[duplicate_mask].index
        for idx in duplicate_indices:
            approval_cell_value = txn_df.at[idx, approval_column]  # noqa: PD008
            is_approved: bool = bool(pd.notna(approval_cell_value)) and (  # ty: ignore[no-matching-overload]
                str(approval_cell_value).strip().upper() == approval_value.upper()
            )

            (approved_mask if is_approved else rejected_mask).loc[idx] = True

        return approved_mask, rejected_mask

    @staticmethod
    def _log_duplicates(
        txn_df: pd.DataFrame,
        approved_mask: pd.Series,
        rejected_mask: pd.Series,
        duplicate_type: str,  # "database" or "intra-import"
    ) -> None:
        """Log the results of duplicate processing.

        Args:
            txn_df: Full transaction DataFrame
            approved_mask: Mask for approved duplicates
            rejected_mask: Mask for rejected duplicates
            duplicate_type: Type of duplicate ("database" or "intra-import")
        """
        # Log approved duplicates
        if approved_mask.any():
            approved_count = approved_mask.sum()
            audit.stage("APPROVE %d %s duplicate(s)", approved_count, duplicate_type)

            audit_rows(AuditMark.APPROVED, txn_df[approved_mask])

        # Log rejected duplicates
        if rejected_mask.any():
            rejected_count = rejected_mask.sum()
            audit.stage(
                "REJECT %d %s duplicate(s), not approved",
                rejected_count,
                duplicate_type,
                level=logging.WARNING,
            )

            audit_rows(AuditMark.REMOVED, txn_df[rejected_mask], level=logging.WARNING)
