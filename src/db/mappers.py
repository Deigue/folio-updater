"""Module to handle mapping related operations."""

import re
from logging import Logger

import pandas as pd

from app.app_context import get_config
from db.utils import format_transaction_summary
from utils.constants import TXN_ESSENTIALS
from utils.logging_setup import get_import_logger

import_logger: Logger = get_import_logger()


class TransactionMapper:
    """Class to handle mapping of transaction DataFrame columns."""

    @staticmethod
    def map_headers(excel_df: pd.DataFrame) -> pd.DataFrame:
        """Map DataFrame columns from Excel headers to internal names."""
        config = get_config()
        header_keywords = config.header_keywords

        norm_keywords: dict[str, set[str]] = {
            internal: {TransactionMapper._normalize(kw) for kw in keywords}
            for internal, keywords in header_keywords.items()
        }

        mapping: dict[str, str] = {}
        unmatched = set(TXN_ESSENTIALS)  # copy of essential fields to match

        for column in excel_df.columns:
            normalized_column = TransactionMapper._normalize(column)
            for internal, keywords in norm_keywords.items():
                if normalized_column in keywords and internal in unmatched:
                    mapping[column] = internal
                    unmatched.remove(internal)
                    break

        if mapping:
            pretty_mapping = "\n".join(f'"{k}" -> "{v}"' for k, v in mapping.items())
            import_logger.debug("Excel->Internal mappings:\n%s", pretty_mapping)
        else:
            import_logger.debug("Excel->Internal mappings: {}")  # pragma: no cover

        # Ensure TXN_ESSENTIALS are present in the mapping
        if unmatched:
            error_message = f"Could not map essential columns: {unmatched}"
            import_logger.error(error_message)
            raise ValueError(error_message)

        excel_df = excel_df.rename(columns=mapping)
        summaries = excel_df.apply(format_transaction_summary, axis=1)
        for summary in summaries:
            import_logger.info(" - %s", summary)
        return excel_df

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
