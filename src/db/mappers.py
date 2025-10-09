"""Module to handle mapping related operations."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from app.app_context import get_config
from db.utils import format_transaction_summary
from utils.constants import TXN_ESSENTIALS, Column
from utils.logging_setup import get_import_logger

if TYPE_CHECKING:
    from logging import Logger

    import pandas as pd

import_logger: Logger = get_import_logger()


class TransactionMapper:
    """Class to handle mapping of transaction DataFrame columns."""

    @staticmethod
    def map_headers(df: pd.DataFrame, account: str | None = None) -> pd.DataFrame:
        """Map DataFrame columns from Excel headers to internal names.

        Args:
            df: DataFrame with raw header names.
            account: Optional account identifier to use as fallback when
                Account column is missing from the Excel file.

        Returns:
            DataFrame with mapped headers and account column populated.
        """
        config = get_config()
        header_keywords = config.header_keywords
        header_ignore = config.header_ignore
        optional_fields = config.optional_fields

        normalized_ignore = {TransactionMapper._normalize(col) for col in header_ignore}
        norm_keywords: dict[str, set[str]] = {
            internal: {TransactionMapper._normalize(kw) for kw in keywords}
            for internal, keywords in header_keywords.items()
        }

        # Add optional fields keywords to the mapping
        for field_name, field_config in optional_fields.get_all_fields().items():
            norm_keywords[field_name] = {
                TransactionMapper._normalize(kw) for kw in field_config.keywords
            }

        mapping, unmatched, ignored_columns = TransactionMapper._process_columns(
            df.columns,
            normalized_ignore,
            norm_keywords,
        )

        if ignored_columns:
            import_logger.info("IGNORE columns: %s", ignored_columns)
            df = df.drop(columns=ignored_columns)

        if mapping:
            pretty_mapping = "\n".join(f'"{k}" -> "{v}"' for k, v in mapping.items())
            import_logger.debug("Excel->Internal mappings:\n%s", pretty_mapping)
        else:
            import_logger.debug("Excel->Internal mappings: {}")  # pragma: no cover

        # Ensure TXN_ESSENTIALS are present in the mapping
        if unmatched and Column.Txn.ACCOUNT in unmatched and account is not None:
            import_logger.info(
                "FALLBACK account column using: %s",
                account,
            )
            df[Column.Txn.ACCOUNT] = account
            unmatched.remove(Column.Txn.ACCOUNT)

        if unmatched:
            unmatched_str = {str(col) for col in unmatched}
            error_message = f"MISSING essential columns: {unmatched_str}"
            import_logger.error(error_message)
            raise ValueError(error_message)

        df = df.rename(columns=mapping)

        if import_logger.isEnabledFor(logging.INFO):
            summaries = df.apply(format_transaction_summary, axis=1)
            for summary in summaries:
                import_logger.info(" + %s", summary)
        return df

    @staticmethod
    def remove_approval_column(txn_df: pd.DataFrame) -> pd.DataFrame:
        """Remove the duplicate approval column from the DataFrame if it exists."""
        config = get_config()
        approval_column = config.duplicate_approval_column
        if approval_column in txn_df.columns:
            msg = f"Duplicate approval column found: {approval_column}"
            import_logger.debug(msg)
            return txn_df.drop(columns=[approval_column])
        return txn_df

    @staticmethod
    def _process_columns(
        columns: pd.Index,
        normalized_ignore: set[str],
        norm_keywords: dict[str, set[str]],
    ) -> tuple[dict[str, str], set[str], list[str]]:
        """Process columns, track unmatched essentials, and identify ignored columns.

        Args:
            columns: The columns from the DataFrame.
            normalized_ignore: Set of normalized ignore patterns.
            norm_keywords: Dictionary of internal names to normalized keywords.

        Returns:
            Tuple of (mapping dict, unmatched set, ignored columns list).
        """
        mapping: dict[str, str] = {}
        unmatched = set(TXN_ESSENTIALS)  # copy of essential fields to match
        ignored_columns = []

        for column in columns:
            normalized_column = TransactionMapper._normalize(column)

            if TransactionMapper._should_ignore_column(
                normalized_column,
                normalized_ignore,
                norm_keywords,
            ):
                ignored_columns.append(column)
                continue

            # Map the column if it matches keywords
            for internal, keywords in norm_keywords.items():
                if normalized_column in keywords:
                    mapping[column] = internal
                    unmatched.discard(internal)
                    break

        return mapping, unmatched, ignored_columns

    @staticmethod
    def _should_ignore_column(
        normalized_column: str,
        normalized_ignore: set[str],
        norm_keywords: dict[str, set[str]],
    ) -> bool:
        """Check if a column should be ignored.

        Args:
            normalized_column: The normalized column name
            normalized_ignore: Set of normalized ignore patterns
            norm_keywords: Dictionary of internal names to normalized keywords

        Returns:
            True if the column should be ignored, False otherwise
        """
        if normalized_column not in normalized_ignore:
            return False

        # Don't ignore essential columns even if they're in the ignore list
        is_essential = any(
            normalized_column in keywords
            for internal, keywords in norm_keywords.items()
            if internal in TXN_ESSENTIALS
        )

        if is_essential:
            import_logger.warning(
                "KEEP column '%s' (in ignore list but essential)",
                normalized_column,
            )
            return False

        return True

    @staticmethod
    def _normalize(name: str) -> str:
        """Normalize string for the database.

        Normalizes the inputstring by converting to lowercase and removing
        non-alphanumeric characters. (except $)

        Args:
            name: The input string to normalize

        Returns:
            str: The normalized string, or empty string if applicable

        Example:
            >>> _normalize("  Transaction Date  ")
            'transactiondate'
        """
        name = name.strip().lower()
        if not name:  # pragma: no cover
            return name
        return re.sub(r"[^a-z0-9$]", "", name)
