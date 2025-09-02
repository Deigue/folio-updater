"""Tests for excel_importer module."""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING, Callable

import pandas as pd
import pandas.testing as pd_testing
import pytest

from db import db
from db.db import get_connection
from importers.excel_importer import import_transactions
from mock.folio_setup import ensure_folio_exists
from utils.constants import TXN_ESSENTIALS, Table

if TYPE_CHECKING:
    from contextlib import _GeneratorContextManager

    from app.app_context import AppContext
    from utils.config import Config

logger: logging.Logger = logging.getLogger(__name__)

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 200)
pd.set_option("display.max_colwidth", None)


def test_import_transactions(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    with temp_config() as ctx:
        config = ctx.config
        ensure_folio_exists()

        default_df = _get_default_dataframe(config)

        _test_duplicate_import(config, default_df)
        _test_empty_db_import(config, default_df)
        _test_missing_essential_column(config, default_df)
        df_with_optional = _test_optional_columns_import(config, default_df)
        _test_additional_columns_with_scrambled_order(config, df_with_optional)
        _test_lesser_columns_import(config, default_df)
        _debug_db_structure()


def test_intra_import_duplicates(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    with temp_config() as ctx:
        config = ctx.config
        ensure_folio_exists()

        default_df = _get_default_dataframe(config)
        # Duplicate the first row to create a duplicate transaction
        df_with_dupes = pd.concat([default_df, default_df.iloc[[0]]], ignore_index=True)
        txn_sheet = config.transactions_sheet()
        temp_path = config.folio_path.parent / "temp_intra_dupes.xlsx"
        df_with_dupes.to_excel(temp_path, index=False, sheet_name=txn_sheet)

        # Import should only add one unique transaction for the duplicate row
        config.db_path.unlink()  # Start with empty DB
        imported_count = import_transactions(temp_path)
        assert imported_count == len(default_df)
        _verify_db_contents(default_df, last_n=len(default_df))


def _get_default_dataframe(config: Config) -> pd.DataFrame:
    """Get the default DataFrame from the transactions sheet."""
    txn_sheet = config.transactions_sheet()
    return pd.read_excel(config.folio_path, sheet_name=txn_sheet)


def _test_duplicate_import(config: Config, default_df: pd.DataFrame) -> None:
    """Test importing duplicate transactions results in 0 new imports."""
    transactions = import_transactions(config.folio_path)
    assert transactions == 0
    _verify_db_contents(default_df, last_n=len(default_df))


def _test_empty_db_import(config: Config, default_df: pd.DataFrame) -> None:
    """Test importing into an empty database."""
    config.db_path.unlink()
    assert import_transactions(config.folio_path) > 0
    _verify_db_contents(default_df, last_n=len(default_df))


def _test_missing_essential_column(config: Config, default_df: pd.DataFrame) -> None:
    """Test importing with missing essential column should raise ValueError."""
    df = default_df.copy()
    essential_to_remove = next(iter(TXN_ESSENTIALS))

    if essential_to_remove not in df.columns:  # pragma: no cover
        msg = f"Essential column '{essential_to_remove}' is missing."
        raise ValueError(msg)

    df = df.drop(columns=[essential_to_remove])
    txn_sheet = config.transactions_sheet()
    temp_path = config.folio_path.parent / "temp_missing_essential.xlsx"
    df.to_excel(temp_path, index=False, sheet_name=txn_sheet)

    with pytest.raises(
        ValueError,
        match=rf"Could not map essential columns: \{{'{essential_to_remove}'\}}\s*",
    ):
        import_transactions(temp_path)


def _add_extra_columns_to_df(df: pd.DataFrame, extra_cols: dict) -> pd.DataFrame:
    """Add extra columns to DataFrame with proper type conversion."""
    for col, data in extra_cols.items():
        if isinstance(data, list):
            df[col] = pd.Series(data).astype(str)
        else:
            df[col] = data.astype(str)
    return df


def _modify_essential_for_uniqueness(df: pd.DataFrame, suffix: str) -> pd.DataFrame:
    """Modify an essential column's data to make rows unique for testing."""
    df = df.copy()
    if "Ticker" in df.columns:  # pragma: no branch
        df["Ticker"] = df["Ticker"].astype(str) + suffix
    return df


def _test_optional_columns_import(
    config: Config,
    default_df: pd.DataFrame,
) -> pd.DataFrame:
    """Test importing with optional columns."""
    df = default_df.copy()
    extra_cols = {
        "ExtraCol1": ["foo"] * len(default_df),
        "ExtraCol2": ["123"] * len(default_df),
        "ExtraCol3": pd.date_range("2020-01-01", periods=len(default_df)),
    }
    df = _add_extra_columns_to_df(df, extra_cols)
    df = _modify_essential_for_uniqueness(df, "_optional")

    txn_sheet = config.transactions_sheet()
    temp_path = config.folio_path.parent / "temp_optional_columns.xlsx"
    df.to_excel(temp_path, index=False, sheet_name=txn_sheet)
    assert import_transactions(temp_path) > 0
    _verify_db_contents(df, last_n=len(df))
    return df


def _test_additional_columns_with_scrambled_order(
    config: Config,
    baseline_df: pd.DataFrame,
) -> None:
    """Test importing with additional columns and scrambled column order."""
    df = baseline_df.copy()
    more_extra_cols = {
        "ExtraCol4": ["bar"] * len(df),
        "ExtraCol5": [456] * len(df),
        "ExtraCol6": pd.date_range("2021-02-01", periods=len(df)),
    }
    df = _add_extra_columns_to_df(df, more_extra_cols)
    df = _modify_essential_for_uniqueness(df, "_scrambled")

    cols = list(df.columns)
    random.shuffle(cols)
    logger.debug("Shuffled columns: %s", cols)
    df_ordered = df.copy()
    df = df[cols]

    txn_sheet = config.transactions_sheet()
    temp_path = config.folio_path.parent / "temp_scrambled_columns.xlsx"
    df.to_excel(temp_path, index=False, sheet_name=txn_sheet)
    assert import_transactions(temp_path) > 0
    _verify_db_contents(df_ordered, last_n=len(df))


def _test_lesser_columns_import(config: Config, default_df: pd.DataFrame) -> None:
    """Test importing with fewer columns than internal database."""
    df = default_df.copy()
    extra_cols = {
        "ExtraCol7": ["wat"] * len(default_df),
        "ExtraCol8": [789] * len(default_df),
        "ExtraCol9": pd.date_range("2022-03-01", periods=len(default_df)),
    }
    df = _add_extra_columns_to_df(df, extra_cols)
    df = _modify_essential_for_uniqueness(df, "_lesser")

    txn_sheet = config.transactions_sheet()
    temp_path = config.folio_path.parent / "lesser_columns.xlsx"
    df.to_excel(temp_path, index=False, sheet_name=txn_sheet)
    assert import_transactions(temp_path) > 0

    # Pad df with missing columns that exist in the database
    with get_connection() as conn:
        query = f'SELECT * FROM "{Table.TXNS.value}"'  # noqa: S608
        table_df = pd.read_sql_query(query, conn)
        for col in table_df.columns:
            if col not in df.columns:
                df[col] = pd.NA
        df = df[table_df.columns]  # Reorder columns to match DB

    _verify_db_contents(df, last_n=len(df))


def _debug_db_structure() -> None:
    """Debug database structure by printing tables and their contents."""
    with get_connection() as conn:
        # Print tables in db.
        tables = db.get_tables(conn)
        logger.debug("Tables in DB: %s", tables)

        # Print contents of each table
        for table in tables:
            logger.debug("=== %s ===", table)
            table_df = db.get_rows(conn, table)
            logger.debug("\n%s", table_df.to_string(index=False))


def _verify_db_contents(df: pd.DataFrame, last_n: int | None = None) -> None:
    with get_connection() as conn:
        query = f'SELECT * FROM "{Table.TXNS.value}"'  # noqa: S608
        table_df = pd.read_sql_query(query, conn)
        if last_n is not None:  # pragma: no branch
            table_df = table_df.tail(last_n).reset_index(drop=True)
            df = df.reset_index(drop=True)

        # Normalize null values to None for consistent comparison
        df = df.where(pd.notna(df), None)
        table_df = table_df.where(pd.notna(table_df), None)

        try:
            pd_testing.assert_frame_equal(
                df,
                table_df,
            )
        except AssertionError as e:  # pragma: no cover
            logger.info("DataFrame mismatch between imported data and DB contents:")
            logger.info("Imported DataFrame:")
            logger.info("\n%s", df.to_string(index=False))
            logger.info("DB DataFrame:")
            logger.info("\n%s", table_df.to_string(index=False))
            msg = f"DataFrames are not equal: {e}"
            raise AssertionError(msg) from e
