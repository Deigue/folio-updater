"""Prepares data for database insertion."""

from __future__ import annotations

import logging

import pandas as pd

from db import schema_manager, table_manager
from db.filters import TransactionFilter
from db.formatters import TransactionFormatter
from db.mappers import TransactionMapper
from utils.logging_setup import get_import_logger

logger = logging.getLogger(__name__)
import_logger = get_import_logger()


def prepare_transactions(
    excel_df: pd.DataFrame, account: str | None = None,
) -> pd.DataFrame:
    """Transform a transaction DataFrame from Excel to match the current Txns schema.

    Steps:
    1. Map Excel headers to internal transaction fields using HEADER_KEYWORDS.
    2. Format and validate all data fields, excluding invalid rows.
    3. Ensure all TXN_ESSENTIALS are present.
    4. Filter out duplicate transactions within the import itself.
    5. Filter out duplicate transactions already in DB.
    6. Merge with existing Txns schema so prior optional columns are preserved.
    7. Append any new optional columns from Excel to the schema.
    8. Reorder: TXN_ESSENTIALS -> existing optionals -> new optionals.

    Args:
        excel_df: DataFrame read from Excel with transaction data.
        account: Optional account identifier to use as fallback when
            Account column is missing from the Excel file.

    Returns:
        DataFrame ready for DB insertion with correct columns and order.

    """
    if excel_df.empty:  # pragma: no cover
        return excel_df

    schema_manager.create_txns_table()
    txn_df = TransactionMapper.map_headers(excel_df, account)
    txn_df = TransactionFormatter.format_and_validate(txn_df)
    txn_df = TransactionFilter.filter_intra_import_duplicates(txn_df)
    txn_df = TransactionFilter.filter_db_duplicates(txn_df)
    final_columns = table_manager.sync_txns_table_columns(txn_df)

    import_logger.debug("Final ordered columns: %s", final_columns)

    # Add any missing columns to the DataFrame
    for col in final_columns:
        if col not in txn_df.columns:
            txn_df[col] = pd.NA

    return txn_df[final_columns]
