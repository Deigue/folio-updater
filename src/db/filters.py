"""Filter related operations on the database."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import TYPE_CHECKING

import pandas as pd

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
            DataFrame with database duplicates removed.
        """
        if txn_df.empty:  # pragma: no cover
            return txn_df

        existing_keys = TransactionFilter._get_db_transaction_keys()
        if not existing_keys:  # pragma: no cover
            return txn_df

        new_keys_series: pd.Series[str] = txn_df.apply(
            TransactionFilter._generate_key,
            axis=1,
        )
        new_keys: set[str] = set(new_keys_series)
        duplicates: set[str] = existing_keys & new_keys
        if not duplicates:  # pragma: no cover
            return txn_df

        import_logger.info(
            "Filtered %d database duplicate transactions.",
            len(duplicates),
        )

        is_duplicate: pd.Series[bool] = new_keys_series.isin(duplicates)
        duplicates_df: pd.DataFrame = txn_df[is_duplicate]
        summaries = duplicates_df.apply(format_transaction_summary, axis=1)
        for summary in summaries:
            import_logger.info(" - %s", summary)

        return txn_df[~is_duplicate].copy()

    @staticmethod
    def filter_intra_import_duplicates(txn_df: pd.DataFrame) -> pd.DataFrame:
        """Filter out duplicate transactions within the DataFrame itself.

        Args:
            txn_df: DataFrame with transaction data.

        Returns:
            DataFrame with duplicates removed
        """
        if txn_df.empty:  # pragma: no cover
            return txn_df

        keys = txn_df.apply(TransactionFilter._generate_key, axis=1)
        duplicate_mask = keys.duplicated(keep="first")
        num_dupes = duplicate_mask.sum()

        if num_dupes > 0:
            duplicate_transactions = txn_df[duplicate_mask]
            import_logger.info(
                "Filtered %d intra-import duplicate transactions.",
                num_dupes,
            )
            summaries = duplicate_transactions.apply(format_transaction_summary, axis=1)
            for summary in summaries:
                import_logger.info(" - %s", summary)

        return txn_df[~duplicate_mask].copy()

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
                query = f'SELECT {essential_cols} FROM "{Table.TXNS.value}"'  # noqa: S608
                existing_df = pd.read_sql_query(query, conn)
                if existing_df.empty:  # pragma: no cover
                    return set()

                existing_keys: set[str] = set()
                existing_keys.update(
                    existing_df.apply(TransactionFilter._generate_key, axis=1),
                )
            except (sqlite3.Error, pd.errors.DatabaseError):  # pragma: no cover
                import_logger.debug(
                    "Table '%s' does not exist yet, no existing transactions to check.",
                    Table.TXNS.value,
                )
                return set()
            else:
                return existing_keys
