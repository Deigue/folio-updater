"""Utility functions for DataFrame related operations in tests."""

from __future__ import annotations

import logging

import pandas as pd
import pandas.testing as pd_testing

from db.db import get_connection
from utils.constants import TXN_ESSENTIALS, Column, Table

logger: logging.Logger = logging.getLogger(__name__)


def _normalize_dataframes(
    imported_df: pd.DataFrame,
    table_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Normalize DataFrames for comparison by handling data types and null values."""
    # Normalize null values to None for consistent comparison
    imported_df = imported_df.where(pd.notna(imported_df), None)
    table_df = table_df.where(pd.notna(table_df), None)

    # Ensure numeric columns have the same dtype for comparison
    numeric_cols = ["Amount", "Price", "Units"]
    for col in numeric_cols:
        if col in imported_df.columns and col in table_df.columns:
            try:
                imported_df[col] = imported_df[col].astype(float)
                table_df[col] = table_df[col].astype(float)
            except (ValueError, TypeError) as e:
                logger.warning("Could not convert column '%s' to float: %s", col, e)

    # Expected Data Formatters
    if Column.Txn.TICKER in imported_df.columns:
        imported_df[Column.Txn.TICKER] = imported_df[Column.Txn.TICKER].str.upper()

    return imported_df, table_df


def _drop_calculated_columns(
    imported_df: pd.DataFrame,
    table_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Drop the calculated columns from the provided DataFrames."""
    imported_df = imported_df.drop(
        columns=[Column.Txn.SETTLE_CALCULATED, Column.Txn.SETTLE_DATE],
        errors="ignore",
    )
    table_df = table_df.drop(
        columns=[Column.Txn.SETTLE_CALCULATED, Column.Txn.SETTLE_DATE],
        errors="ignore",
    )
    return imported_df, table_df


def _verify_column_order(table_df: pd.DataFrame) -> None:
    """Verify that the database table has expected column ordering."""
    table_cols = list(table_df.columns)
    expected_start = [Column.Txn.TXN_ID, *TXN_ESSENTIALS]
    actual_start = table_cols[: len(expected_start)]

    if actual_start != expected_start:
        error_msg = (f"Expected: {expected_start}, Got start of table: {actual_start}",)
        raise AssertionError(error_msg)


def verify_db_contents(df: pd.DataFrame, last_n: int | None = None) -> None:
    """Verify that the contents of the provided DataFrame match the database table.

    Args:
        df (pd.DataFrame): The DataFrame to verify against the database.
        last_n (int | None): If provided, only the last N rows will be compared.

    Raises:
        AssertionError: If the DataFrame contents do not match the database table.
    """
    imported_df = df.copy()
    with get_connection() as conn:
        query = f'SELECT * FROM "{Table.TXNS}"'
        table_df = pd.read_sql_query(query, conn)

        if last_n is not None:
            table_df = table_df.tail(last_n).reset_index(drop=True)
            imported_df = imported_df.reset_index(drop=True)

        # Normalize DataFrames for comparison
        imported_df, table_df = _normalize_dataframes(imported_df, table_df)

        # Verify column ordering
        _verify_column_order(table_df)

        # Remove TxnId column from table_df for comparison since
        # it's auto-generated and not part of the input data
        if Column.Txn.TXN_ID in table_df.columns:
            table_df = table_df.drop(columns=[Column.Txn.TXN_ID])

        # We don't compare calculated columns
        imported_df, table_df = _drop_calculated_columns(imported_df, table_df)

        # Reorder imported_df to match database column order for comparison
        common_columns = [col for col in table_df.columns if col in imported_df.columns]
        imported_df = imported_df.reindex(columns=common_columns)
        table_df = table_df.reindex(columns=common_columns)

        try:
            pd_testing.assert_frame_equal(imported_df, table_df)
        except AssertionError as e:
            logger.info("DataFrame mismatch between imported data and DB contents:")
            logger.info("Imported DataFrame:")
            logger.info("\n%s", imported_df.to_string(index=False))
            logger.info("DB DataFrame:")
            logger.info("\n%s", table_df.to_string(index=False))
            msg = f"DataFrames are not equal: {e}"
            raise AssertionError(msg) from e
