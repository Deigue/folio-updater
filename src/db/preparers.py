"""Prepares data for database insertion."""

from __future__ import annotations

import logging

import pandas as pd

from db import schema_manager, table_manager
from db.filters import TransactionFilter
from db.formatters import TransactionFormatter
from db.mappers import TransactionMapper
from db.transformers import TransactionTransformer
from utils.logging_setup import get_import_logger

logger = logging.getLogger(__name__)
import_logger = get_import_logger()


def prepare_transactions(
    df: pd.DataFrame,
    account: str | None = None,
) -> pd.DataFrame:
    """Prepare DataFrame for database insertion by applying filters and mappings.

    Args:
        df: Raw transaction DataFrame
        account: Optional account identifier to use as fallback when Account
            column is missing from the Excel file.

    Returns:
        Prepared DataFrame ready for database insertion
    """
    if df.empty:  # pragma: no cover
        return df

    import_logger.debug(
        "PREPARE transactions: mapping → transforming → formatting → filtering",
    )
    schema_manager.create_txns_table()
    txn_df = TransactionMapper.map_headers(df, account)
    txn_df = TransactionTransformer.transform(txn_df)
    txn_df = TransactionFormatter.format_and_validate(txn_df)
    txn_df = TransactionFilter.filter_intra_import_duplicates(txn_df)
    txn_df = TransactionFilter.filter_db_duplicates(txn_df)
    txn_df = TransactionMapper.remove_approval_column(txn_df)
    final_columns = table_manager.sync_txns_table_columns(txn_df)

    # Add any missing columns to the DataFrame
    for col in final_columns:
        if col not in txn_df.columns:
            txn_df[col] = pd.NA

    return txn_df[final_columns]
