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
from utils.constants import TXN_ESSENTIALS, Column, Table

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


def test_import_transactions_intra_dupes(
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


def test_import_transactions_formatting(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test importing transactions with various formatting scenarios.

    This test covers:
    1. Good cases with perfect data
    2. Auto-formatted cases that are successfully imported
    3. Invalid cases that result in row rejection

    For each column type (date, action, amount, currency, price, units, ticker),
    we test:
    - Valid values
    - Auto-formattable values (where applicable)
    - Invalid/empty values that cause rejection

    At the end, we verify that only valid rows are in the database.
    """
    with temp_config() as ctx:
        config = ctx.config
        ensure_folio_exists()

        txn_sheet = config.transactions_sheet()
        temp_path = config.folio_path.parent / "temp_formatting_scenarios.xlsx"

        # Each row represents a specific test scenario
        test_data = {
            Column.Txn.TXN_DATE.value: [
                "2023-01-01",  # Good case - all columns perfect
                "01/02/2023",  # Auto-formatted date (MM/DD/YYYY -> YYYY-MM-DD)
                "2023-01-03",  # Good date with ticker case formatting
                "INVALID_DATE",  # Invalid date - should be rejected
                "",  # Empty date - should be rejected
                "2023-01-05",  # Missing action - should be rejected
                "2023-01-06",  # Invalid action - should be rejected
                "2023-01-07",  # Action abbreviation (will be normalized)
                "2023-01-08",  # Empty amount - should be rejected
                "2023-01-09",  # Invalid amount format - should be rejected
                "2023-01-10",  # Invalid currency - should be rejected
                "2023-01-11",  # Missing currency - should be rejected
                "2023-01-12",  # Alternative currency format (will be normalized)
                "2023-01-13",  # Empty price - should be rejected
                "2023-01-14",  # Invalid price format - should be rejected
                "2023-01-15",  # Empty units - should be rejected
                "2023-01-16",  # Invalid units format - should be rejected
                "2023-01-17",  # Empty ticker (valid - becomes NULL)
                "2023-01-18",  # NULL ticker (valid - stays NULL)
                "2023-01-19",  # Invalid ticker format - should be rejected
                "2023-01-20",  # Multiple invalid: empty amount, invalid price/units
                "2023-01-21",  # Multiple invalid: no currency, bad ticker/action
            ],
            Column.Txn.ACTION.value: [
                "BUY",  # Good case
                "SELL",  # Good case
                "BUY",  # Good case
                "BUY",  # Good case, but date is invalid
                "BUY",  # Good case, but date is empty
                None,  # Missing action
                "INVALID_ACTION",  # Invalid action
                "B",  # Abbreviation, should normalize to "BUY"
                "BUY",  # Good case, but amount is empty
                "BUY",  # Good case, but amount is invalid
                "SELL",  # Good case, but currency is invalid
                "SELL",  # Good case, but currency is missing
                "SELL",  # Good case with alternative currency
                "BUY",  # Good case, but price is empty
                "BUY",  # Good case, but price is invalid
                "SELL",  # Good case, but units is empty
                "SELL",  # Good case, but units is invalid
                "BUY",  # Good case with empty ticker
                "SELL",  # Good case with NULL ticker
                "BUY",  # Good case, but ticker is invalid
                "BUY",  # Multiple invalid: empty amount, invalid price/units
                "INVALID_ACTION",  # Multiple invalid: no currency, bad ticker/action
            ],
            Column.Txn.AMOUNT.value: [
                1000.0,  # Good case
                2000.0,  # Good case
                1500.0,  # Good case
                1000.0,  # Good case, but date is invalid
                1000.0,  # Good case, but date is empty
                1000.0,  # Good case, but action is missing
                1000.0,  # Good case, but action is invalid
                "$1,000.00",  # Currency symbol & commas, should normalize
                "",  # Empty amount - should be rejected
                "INVALID_AMOUNT",  # Invalid amount - should be rejected
                1000.0,  # Good case, but currency is invalid
                1000.0,  # Good case, but currency is missing
                1000.0,  # Good case with alternative currency
                1000.0,  # Good case, but price is empty
                1000.0,  # Good case, but price is invalid
                1000.0,  # Good case, but units is empty
                1000.0,  # Good case, but units is invalid
                1000.0,  # Good case with empty ticker
                1000.0,  # Good case with NULL ticker
                1000.0,  # Good case, but ticker is invalid
                "",  # Multiple invalid: empty amount, invalid price/units
                1000.0,  # Multiple invalid: no currency, bad ticker/action
            ],
            Column.Txn.CURRENCY.value: [
                "USD",  # Good case
                "USD",  # Good case
                "USD",  # Good case
                "USD",  # Good case, but date is invalid
                "USD",  # Good case, but date is empty
                "USD",  # Good case, but action is missing
                "USD",  # Good case, but action is invalid
                "USD",  # Good case with formatted amount
                "USD",  # Good case, but amount is empty
                "USD",  # Good case, but amount is invalid
                "INVALID_CURRENCY",  # Invalid currency - should be rejected
                None,  # Missing currency - should be rejected
                "US$",  # Alternative format, should normalize to "USD"
                "USD",  # Good case, but price is empty
                "USD",  # Good case, but price is invalid
                "USD",  # Good case, but units is empty
                "USD",  # Good case, but units is invalid
                "USD",  # Good case with empty ticker
                "USD",  # Good case with NULL ticker
                "USD",  # Good case, but ticker is invalid
                "USD",  # Multiple invalid: empty amount, invalid price/units
                None,  # Multiple invalid: no currency, bad ticker/action
            ],
            Column.Txn.PRICE.value: [
                100.0,  # Good case
                200.0,  # Good case
                150.0,  # Good case
                100.0,  # Good case, but date is invalid
                100.0,  # Good case, but date is empty
                100.0,  # Good case, but action is missing
                100.0,  # Good case, but action is invalid
                100.0,  # Good case with formatted amount
                100.0,  # Good case, but amount is empty
                100.0,  # Good case, but amount is invalid
                100.0,  # Good case, but currency is invalid
                100.0,  # Good case, but currency is missing
                100.0,  # Good case with alternative currency
                "",  # Empty price - should be rejected
                "INVALID_PRICE",  # Invalid price - should be rejected
                100.0,  # Good case, but units is empty
                100.0,  # Good case, but units is invalid
                100.0,  # Good case with empty ticker
                100.0,  # Good case with NULL ticker
                100.0,  # Good case, but ticker is invalid
                "INVALID_PRICE",  # Multiple invalid: empty amount, invalid price/units
                100.0,  # Multiple invalid: no currency, bad ticker/action
            ],
            Column.Txn.UNITS.value: [
                10.0,  # Good case
                10.0,  # Good case
                10.0,  # Good case
                10.0,  # Good case, but date is invalid
                10.0,  # Good case, but date is empty
                10.0,  # Good case, but action is missing
                10.0,  # Good case, but action is invalid
                10.0,  # Good case with formatted amount
                10.0,  # Good case, but amount is empty
                10.0,  # Good case, but amount is invalid
                10.0,  # Good case, but currency is invalid
                10.0,  # Good case, but currency is missing
                10.0,  # Good case with alternative currency
                10.0,  # Good case, but price is empty
                10.0,  # Good case, but price is invalid
                "",  # Empty units - should be rejected
                "INVALID_UNITS",  # Invalid units - should be rejected
                10.0,  # Good case with empty ticker
                10.0,  # Good case with NULL ticker
                10.0,  # Good case, but ticker is invalid
                "INVALID_UNITS",  # Multiple invalid: empty amount, invalid price/units
                10.0,  # Multiple invalid: no currency, bad ticker/action
            ],
            Column.Txn.TICKER.value: [
                "AAPL",  # Good case
                "MSFT",  # Good case
                "aapl",  # Lowercase ticker - should be uppercased
                "GOOG",  # Good case, but date is invalid
                "AAPL",  # Good case, but date is empty
                "TSLA",  # Good case, but action is missing
                "AMZN",  # Good case, but action is invalid
                "NFLX",  # Good case with formatted amount
                "META",  # Good case, but amount is empty
                "NVDA",  # Good case, but amount is invalid
                "ADBE",  # Good case, but currency is invalid
                "PYPL",  # Good case, but currency is missing
                "PYPL",  # Good case with alternative currency
                "CSCO",  # Good case, but price is empty
                "INTC",  # Good case, but price is invalid
                "CMCSA",  # Good case, but units is empty
                "PEP",  # Good case, but units is invalid
                "",  # Empty ticker - valid (becomes NULL)
                None,  # NULL ticker - valid
                "INVALID@TICKER",  # Invalid ticker format - should be rejected
                "AAPL",  # Multiple invalid: empty amount, invalid price/units
                "INVALID@TICKER",  # Multiple invalid: no currency, bad ticker/action
            ],
        }

        df = pd.DataFrame(test_data)
        df.to_excel(temp_path, index=False, sheet_name=txn_sheet)

        # Clear database
        config.db_path.unlink(missing_ok=True)

        # Import transactions and check the count
        imported_count = import_transactions(temp_path)

        # Create expected dataframe with only valid rows
        expected_rows = [
            # Row 0: Good case - all columns perfect
            {
                Column.Txn.TXN_DATE.value: "2023-01-01",
                Column.Txn.ACTION.value: "BUY",
                Column.Txn.AMOUNT.value: 1000.0,
                Column.Txn.CURRENCY.value: "USD",
                Column.Txn.PRICE.value: 100.0,
                Column.Txn.UNITS.value: 10.0,
                Column.Txn.TICKER.value: "AAPL",
            },
            # Row 1: Auto-formatted date
            {
                Column.Txn.TXN_DATE.value: "2023-01-02",
                Column.Txn.ACTION.value: "SELL",
                Column.Txn.AMOUNT.value: 2000.0,
                Column.Txn.CURRENCY.value: "USD",
                Column.Txn.PRICE.value: 200.0,
                Column.Txn.UNITS.value: 10.0,
                Column.Txn.TICKER.value: "MSFT",
            },
            # Row 2: Ticker case formatting
            {
                Column.Txn.TXN_DATE.value: "2023-01-03",
                Column.Txn.ACTION.value: "BUY",
                Column.Txn.AMOUNT.value: 1500.0,
                Column.Txn.CURRENCY.value: "USD",
                Column.Txn.PRICE.value: 150.0,
                Column.Txn.UNITS.value: 10.0,
                Column.Txn.TICKER.value: "AAPL",  # Uppercased from "aapl"
            },
            # Row 7: Action abbreviation
            {
                Column.Txn.TXN_DATE.value: "2023-01-07",
                Column.Txn.ACTION.value: "BUY",  # Normalized from "B"
                Column.Txn.AMOUNT.value: 1000.0,  # Normalized from "$1,000.00"
                Column.Txn.CURRENCY.value: "USD",
                Column.Txn.PRICE.value: 100.0,
                Column.Txn.UNITS.value: 10.0,
                Column.Txn.TICKER.value: "NFLX",
            },
            # Row 12: Alternative currency format
            {
                Column.Txn.TXN_DATE.value: "2023-01-12",
                Column.Txn.ACTION.value: "SELL",
                Column.Txn.AMOUNT.value: 1000.0,
                Column.Txn.CURRENCY.value: "USD",  # Normalized from "US$"
                Column.Txn.PRICE.value: 100.0,
                Column.Txn.UNITS.value: 10.0,
                Column.Txn.TICKER.value: "PYPL",
            },
            # Row 17: Empty ticker
            {
                Column.Txn.TXN_DATE.value: "2023-01-17",
                Column.Txn.ACTION.value: "BUY",
                Column.Txn.AMOUNT.value: 1000.0,
                Column.Txn.CURRENCY.value: "USD",
                Column.Txn.PRICE.value: 100.0,
                Column.Txn.UNITS.value: 10.0,
                Column.Txn.TICKER.value: pd.NA,  # Empty ticker becomes NULL
            },
            # Row 18: NULL ticker
            {
                Column.Txn.TXN_DATE.value: "2023-01-18",
                Column.Txn.ACTION.value: "SELL",
                Column.Txn.AMOUNT.value: 1000.0,
                Column.Txn.CURRENCY.value: "USD",
                Column.Txn.PRICE.value: 100.0,
                Column.Txn.UNITS.value: 10.0,
                Column.Txn.TICKER.value: pd.NA,  # NULL ticker stays NULL
            },
        ]

        expected_df = pd.DataFrame(expected_rows)

        # Assert correct number of imports
        expected_imports = len(expected_df)
        error_msg = f"Expected {expected_imports} imports but got {imported_count}"
        assert imported_count == expected_imports, error_msg

        # Compare with DB contents
        _verify_db_contents(expected_df)


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
    df = _modify_essential_for_uniqueness(df, ".optional")

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
    df = _modify_essential_for_uniqueness(df, ".scrambled")

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
    df = _modify_essential_for_uniqueness(df, ".lesser")

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
    imported_df = df.copy()
    with get_connection() as conn:
        query = f'SELECT * FROM "{Table.TXNS.value}"'  # noqa: S608
        table_df = pd.read_sql_query(query, conn)
        if last_n is not None:  # pragma: no branch
            table_df = table_df.tail(last_n).reset_index(drop=True)
            imported_df = imported_df.reset_index(drop=True)

        # Normalize null values to None for consistent comparison
        imported_df = imported_df.where(pd.notna(imported_df), None)
        table_df = table_df.where(pd.notna(table_df), None)

        # Ensure numeric columns have the same dtype for comparison
        numeric_cols = ["Amount", "Price", "Units"]
        for col in numeric_cols:
            if (
                col in imported_df.columns and col in table_df.columns
            ):  # pragma: no branch
                try:
                    imported_df[col] = imported_df[col].astype(float)
                    table_df[col] = table_df[col].astype(float)
                except (ValueError, TypeError) as e:  # pragma: no cover
                    logger.warning("Could not convert column '%s' to float: %s", col, e)

        # Formatters
        imported_df[Column.Txn.TICKER] = imported_df[Column.Txn.TICKER].str.upper()

        try:
            pd_testing.assert_frame_equal(
                imported_df,
                table_df,
            )
        except AssertionError as e:  # pragma: no cover
            logger.info("DataFrame mismatch between imported data and DB contents:")
            logger.info("Imported DataFrame:")
            logger.info("\n%s", imported_df.to_string(index=False))
            logger.info("DB DataFrame:")
            logger.info("\n%s", table_df.to_string(index=False))
            msg = f"DataFrames are not equal: {e}"
            raise AssertionError(msg) from e
