"""The prepare-transactions pipeline, start to finish."""

from __future__ import annotations

import pandas as pd

from app import audit

# Local imports, don;t get from db package
from db import schema
from ingest.dedupe import TransactionFilter
from ingest.mappers import TransactionMapper
from ingest.rules import TransactionTransformer
from ingest.validation import TransactionFormatter
from models import ImportResults, new_txns_from_frame


def prepare_transactions(
    df: pd.DataFrame,
    account: str | None = None,
    *,
    map_headers: bool = True,
) -> ImportResults:
    """Prepare DataFrame for database insertion by applying filters and mappings.

    Args:
        df: Source rows DataFrame
        account: Optional fallback account name
        map_headers: If True, map external headers to internal field names. Set
            False when the DataFrame is already keyed by internal names (e.g.
            transactions built by the `add` command).

    Returns:
        Returns ImportResults with all stages of the preparation process.
    """
    if df.empty:  # pragma: no cover
        return ImportResults()

    audit.debug(
        "PREPARE transactions: mapping → transforming → formatting → filtering",
    )
    schema.create_txns_table()
    schema.create_ticker_aliases_table()

    read_df = df.copy()
    mapped_df = (
        TransactionMapper.map_headers(read_df, account) if map_headers else read_df
    )
    ruled = TransactionTransformer.transform(mapped_df)
    formatted_df, invalid_df = TransactionFormatter.format_and_validate(ruled.df)
    invalid_df = _with_invalid_cancellations(ruled.invalid, invalid_df)
    intra_approved_df = TransactionFilter.filter_intra_import_duplicates(formatted_df)
    db_approved_df = TransactionFilter.filter_db_duplicates(intra_approved_df)
    cleaned_df = TransactionFilter.remove_approval_column(db_approved_df)

    return ImportResults(
        read_df=read_df,
        invalid_df=invalid_df,
        intra_rejected_df=_rejected(formatted_df, intra_approved_df),
        db_rejected_df=_rejected(intra_approved_df, db_approved_df),
        approved=new_txns_from_frame(cleaned_df),
        merge_events=ruled.merges,
        cancel_events=ruled.cancels,
        transform_events=ruled.transforms,
    )


def _with_invalid_cancellations(
    cancellations: pd.DataFrame,
    invalid: pd.DataFrame,
) -> pd.DataFrame:
    """Reject cancellations with nothing to void alongside the invalid rows."""
    if cancellations.empty:
        return invalid
    if invalid.empty:
        return cancellations
    return pd.concat([cancellations, invalid])


def _rejected(before: pd.DataFrame, after: pd.DataFrame) -> pd.DataFrame:
    """Return the rows a filter took out of `before`, leaving `after`."""
    return before.loc[before.index.difference(after.index)].copy()
