"""Filter related operations on the database."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import TYPE_CHECKING

import pandas as pd

from app.app_context import get_config
from db import db
from db.utils import format_transaction_summary
from utils.constants import TXN_ESSENTIALS, Table
from utils.logging_setup import get_import_logger

if TYPE_CHECKING:
    from logging import Logger

import_logger: Logger = get_import_logger()


class TransactionFilter:
    """Class to filter out duplicate transactions."""

    @staticmethod
    def filter_db_duplicates(txn_df: pd.DataFrame) -> pd.DataFrame:
        """Filter out transactions that already exist in the database.

        Args:
            txn_df: DataFrame with transaction data.

        Returns:
            DataFrame with database duplicates removed, unless approved.
        """
        if txn_df.empty:  # pragma: no cover
            return txn_df

        existing_keys = TransactionFilter._get_db_transaction_keys()
        if not existing_keys:
            return txn_df

        new_keys_series: pd.Series[str] = txn_df.apply(
            TransactionFilter._generate_key,
            axis=1,
        )
        new_keys: set[str] = set(new_keys_series)
        import_logger.debug(
            "CHECK duplicates: %d existing keys, %d new keys",
            len(existing_keys),
            len(new_keys),
        )
        duplicates: set[str] = existing_keys & new_keys
        if not duplicates:  # pragma: no cover
            return txn_df

        is_duplicate: pd.Series[bool] = new_keys_series.isin(duplicates)
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
        """Filter out duplicate transactions within the DataFrame itself.

        Args:
            txn_df: DataFrame with transaction data.

        Returns:
            DataFrame with duplicates removed, unless approved.
        """
        if txn_df.empty:  # pragma: no cover
            return txn_df

        keys = txn_df.apply(TransactionFilter._generate_key, axis=1)
        duplicate_mask = keys.duplicated(keep=False)
        num_dupes = duplicate_mask.sum()

        if num_dupes == 0:
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
            approval_cell_value = txn_df.loc[idx, approval_column]
            is_approved: bool = (
                pd.notna(approval_cell_value)
                and str(approval_cell_value).strip().upper() == approval_value.upper()
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
            msg = f"APPROVE {approved_count} {duplicate_type} duplicates"
            import_logger.info(msg)
            approved_summaries = txn_df[approved_mask].apply(
                format_transaction_summary,
                axis=1,
            )
            for summary in approved_summaries:
                import_logger.info(" * %s", summary)

        # Log rejected duplicates
        if rejected_mask.any():
            rejected_count = rejected_mask.sum()
            msg = f"SKIP {rejected_count} {duplicate_type} duplicates"
            import_logger.warning(msg)
            rejected_summaries = txn_df[rejected_mask].apply(
                format_transaction_summary,
                axis=1,
            )
            for summary in rejected_summaries:
                import_logger.warning(" - %s", summary)

    @staticmethod
    def _generate_key(row: pd.Series) -> str:
        """Generate a synthetic primary key from TXN_ESSENTIAL columns.

        Args:
            row: A pandas Series containing transaction data.

        Returns:
            A hash string representing the synthetic primary key.
        """

        def normalize_value(val: str | float | None) -> str:
            if pd.isna(val):  # pragma: no cover
                return ""
            # Try to treat as float, format to 8 decimals, else as string
            try:
                fval = float(val)
                # Remove trailing zeros and dot if not needed
                return f"{fval:.8f}".rstrip("0").rstrip(".")
            except (ValueError, TypeError):
                return str(val).strip()

        key_parts = [normalize_value(row.get(col, "")) for col in TXN_ESSENTIALS]
        key_string = "|".join(key_parts)
        return hashlib.sha256(key_string.encode("utf-8")).hexdigest()

    @staticmethod
    def _get_db_transaction_keys() -> set[str]:
        """Get synthetic keys for all existing transactions in the database.

        Args:
            conn: Database connection.

        Returns:
            Set of synthetic keys for existing transactions.
        """
        with db.get_connection() as conn:
            try:
                # Build the query to select essential columns.
                essential_cols = ", ".join(f'"{col}"' for col in TXN_ESSENTIALS)
                query = f'SELECT {essential_cols} FROM "{Table.TXNS}"'
                existing_df = pd.read_sql_query(query, conn)
                if existing_df.empty:  # pragma: no cover
                    return set()

                existing_keys: set[str] = set()
                existing_keys.update(
                    existing_df.apply(TransactionFilter._generate_key, axis=1),
                )
            except (sqlite3.Error, pd.errors.DatabaseError):
                import_logger.debug(
                    "Table '%s' does not exist yet, no existing transactions to check.",
                    Table.TXNS,
                )
                return set()
            else:
                return existing_keys
