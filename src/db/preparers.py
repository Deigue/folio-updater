"""Prepares data for database insertion."""

from __future__ import annotations

import logging

import pandas as pd

from db import schema_manager, table_manager
from db.filters import TransactionFilter
from db.formatters import TransactionFormatter
from db.mappers import TransactionMapper
from db.transformers import TransactionTransformer
from models.import_results import ImportResults
from utils.logging_setup import get_import_logger

logger = logging.getLogger(__name__)
import_logger = get_import_logger()


def prepare_transactions(
    df: pd.DataFrame,
    account: str | None = None,
) -> ImportResults:
    """Prepare DataFrame for database insertion by applying filters and mappings.

    Args:
        df: Raw transaction DataFrame
        account: Optional fallback account name

    Returns:
        Returns ImportResults with all stages of the preparation process.
    """
    if df.empty:  # pragma: no cover
        return ImportResults()

    import_logger.debug(
        "PREPARE transactions: mapping → transforming → formatting → filtering",
    )
    schema_manager.create_txns_table()

    read_df = df.copy()
    mapped_df = TransactionMapper.map_headers(read_df, account)
    transformed_df, merge_events, transform_events = TransactionTransformer.transform(
        mapped_df,
    )
    formatted_df, excluded_df = TransactionFormatter.format_and_validate(transformed_df)
    intra_approved_df = TransactionFilter.filter_intra_import_duplicates(formatted_df)
    db_approved_df = TransactionFilter.filter_db_duplicates(intra_approved_df)
    cleaned_df = TransactionMapper.remove_approval_column(db_approved_df)
    final_columns = table_manager.sync_txns_table_columns(cleaned_df)

    # Add any missing columns to the DataFrame
    for col in final_columns:
        if col not in cleaned_df.columns:
            cleaned_df[col] = pd.NA
    final_df = cleaned_df[final_columns]

    # Calculate rejection DataFrames
    intra_rejected_df = pd.DataFrame()
    if len(formatted_df) > len(intra_approved_df):
        intra_keys = formatted_df.index.difference(intra_approved_df.index)
        intra_rejected_df = formatted_df.loc[intra_keys].copy()

    db_rejected_df = pd.DataFrame()
    if len(intra_approved_df) > len(db_approved_df):
        db_keys = intra_approved_df.index.difference(db_approved_df.index)
        db_rejected_df = intra_approved_df.loc[db_keys].copy()

    return ImportResults(
        read_df=read_df,
        mapped_df=mapped_df,
        transformed_df=transformed_df,
        excluded_df=excluded_df,
        intra_approved_df=intra_approved_df,
        intra_rejected_df=intra_rejected_df,
        db_approved_df=db_approved_df,
        db_rejected_df=db_rejected_df,
        final_df=final_df,
        merge_events=merge_events,
        transform_events=transform_events,
    )
