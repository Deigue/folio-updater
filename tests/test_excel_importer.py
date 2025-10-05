"""Tests for excel_importer module."""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING, Any, Callable

import pandas as pd
import pytest
import yaml

from db import db
from db.db import get_connection
from importers.excel_importer import import_transactions
from mock.folio_setup import ensure_folio_exists
from utils.constants import TXN_ESSENTIALS, Column, Table

from .utils.dataframe_utils import verify_db_contents

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

        # Import should reject ALL duplicates (no first-occurrence logic)
        config.db_path.unlink()  # Start with empty DB
        imported_count = import_transactions(temp_path, "TEST-ACCOUNT", txn_sheet)
        # Should import original transactions minus the duplicated one
        expected_count = len(default_df) - 1
        assert imported_count == expected_count

        # Verify database contains only non-duplicate transactions
        # Skip first row since it was duplicated
        expected_df = default_df.iloc[1:].copy()
        verify_db_contents(expected_df, last_n=expected_count)


def test_import_transactions_formatting(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test importing transactions with various formatting scenarios."""
    with temp_config() as ctx:
        config = ctx.config
        ensure_folio_exists()

        txn_sheet = config.transactions_sheet()
        temp_path = config.folio_path.parent / "temp_formatting_scenarios.xlsx"

        # Each row represents a specific test scenario
        test_data = {
            Column.Txn.TXN_DATE: [
                "2023-01-01",  # Good case - all columns perfect
                "01/02/2023",  # Auto-formatted date (MM/DD/YYYY -> YYYY-MM-DD)
                "2023-01-03T10:30:45Z",  # ISO 8601 format with timezone
                "INVALID_DATE",  # Invalid date - should be rejected
                "",  # Empty date - should be rejected
                "2023-01-05 15:45:30",  # Datetime format with space
                "2023-01-06",  # Invalid action - should be rejected
                "2023-01-07",  # Action abbreviation (will be normalized)
                "2023-01-08",  # Empty amount - should be rejected
                "2023-01-09",  # Invalid amount format - should be rejected
                "2023-01-10",  # Invalid currency - should be rejected
                "2023-01-11",  # Missing currency - should be rejected
                "2023-01-12T20:15:30.123456Z",  # ISO format with ms
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
            Column.Txn.ACTION: [
                "BUY",  # Good case
                "SELL",  # Good case
                "DIVIDEND",  # New action type - dividend payment
                "BUY",  # Good case, but date is invalid
                "BUY",  # Good case, but date is empty
                None,  # Missing action
                "INVALID_ACTION",  # Invalid action
                "DIV",  # Abbreviation, should normalize to "DIVIDEND"
                "BUY",  # Good case, but amount is empty
                "BUY",  # Good case, but amount is invalid
                "SELL",  # Good case, but currency is invalid
                "SELL",  # Good case, but currency is missing
                "CONTRIBUTION",  # New action type - money contribution
                "BUY",  # Good case, but price is empty
                "BUY",  # Good case, but price is invalid
                "SELL",  # Good case, but units is empty
                "SELL",  # Good case, but units is invalid
                "WITHDRAWAL",  # New action type - money withdrawal
                "CONTRIBUTION",  # Good case with NULL ticker
                "BUY",  # Good case, but ticker is invalid
                "BUY",  # Multiple invalid: empty amount, invalid price/units
                "INVALID_ACTION",  # Multiple invalid: no currency, bad ticker/action
            ],
            Column.Txn.AMOUNT: [
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
            Column.Txn.CURRENCY: [
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
            Column.Txn.PRICE: [
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
            Column.Txn.UNITS: [
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
            Column.Txn.TICKER: [
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
        imported_count = import_transactions(temp_path, "TEST-ACCOUNT", txn_sheet)

        # Create expected dataframe with only valid rows (0, 1, 2, 7, 12, 17, 18)
        expected_rows = [
            # Row 0: Good case - all columns perfect
            {
                Column.Txn.TXN_DATE: "2023-01-01",
                Column.Txn.ACTION: "BUY",
                Column.Txn.AMOUNT: 1000.0,
                Column.Txn.CURRENCY: "USD",
                Column.Txn.PRICE: 100.0,
                Column.Txn.UNITS: 10.0,
                Column.Txn.TICKER: "AAPL",
                Column.Txn.ACCOUNT: "TEST-ACCOUNT",
            },
            # Row 1: Auto-formatted date
            {
                Column.Txn.TXN_DATE: "2023-01-02",
                Column.Txn.ACTION: "SELL",
                Column.Txn.AMOUNT: 2000.0,
                Column.Txn.CURRENCY: "USD",
                Column.Txn.PRICE: 200.0,
                Column.Txn.UNITS: 10.0,
                Column.Txn.TICKER: "MSFT",
                Column.Txn.ACCOUNT: "TEST-ACCOUNT",
            },
            # Row 2: ISO 8601 date format, DIVIDEND action, ticker case formatting
            {
                Column.Txn.TXN_DATE: "2023-01-03",
                Column.Txn.ACTION: "DIVIDEND",
                Column.Txn.AMOUNT: 1500.0,
                Column.Txn.CURRENCY: "USD",
                Column.Txn.PRICE: 150.0,
                Column.Txn.UNITS: 10.0,
                Column.Txn.TICKER: "AAPL",  # Uppercased from "aapl"
                Column.Txn.ACCOUNT: "TEST-ACCOUNT",
            },
            # Row 7: Action abbreviation (DIV -> DIVIDEND)
            {
                Column.Txn.TXN_DATE: "2023-01-07",
                Column.Txn.ACTION: "DIVIDEND",  # Normalized from "DIV"
                Column.Txn.AMOUNT: 1000.0,  # Normalized from "$1,000.00"
                Column.Txn.CURRENCY: "USD",
                Column.Txn.PRICE: 100.0,
                Column.Txn.UNITS: 10.0,
                Column.Txn.TICKER: "NFLX",
                Column.Txn.ACCOUNT: "TEST-ACCOUNT",
            },
            # Row 12: ISO format with ms, CONTRIBUTION, alternative currency format
            {
                Column.Txn.TXN_DATE: "2023-01-12",
                Column.Txn.ACTION: "CONTRIBUTION",
                Column.Txn.AMOUNT: 1000.0,
                Column.Txn.CURRENCY: "USD",  # Normalized from "US$"
                Column.Txn.PRICE: 100.0,
                Column.Txn.UNITS: 10.0,
                Column.Txn.TICKER: "PYPL",
                Column.Txn.ACCOUNT: "TEST-ACCOUNT",
            },
            # Row 17: Empty ticker with WITHDRAWAL action
            {
                Column.Txn.TXN_DATE: "2023-01-17",
                Column.Txn.ACTION: "WITHDRAWAL",
                Column.Txn.AMOUNT: 1000.0,
                Column.Txn.CURRENCY: "USD",
                Column.Txn.PRICE: 100.0,
                Column.Txn.UNITS: 10.0,
                Column.Txn.TICKER: pd.NA,  # Empty ticker becomes NULL
                Column.Txn.ACCOUNT: "TEST-ACCOUNT",
            },
            # Row 18: NULL ticker with CONTRIBUTION action
            {
                Column.Txn.TXN_DATE: "2023-01-18",
                Column.Txn.ACTION: "CONTRIBUTION",
                Column.Txn.AMOUNT: 1000.0,
                Column.Txn.CURRENCY: "USD",
                Column.Txn.PRICE: 100.0,
                Column.Txn.UNITS: 10.0,
                Column.Txn.TICKER: pd.NA,  # NULL ticker stays NULL
                Column.Txn.ACCOUNT: "TEST-ACCOUNT",
            },
        ]

        expected_df = pd.DataFrame(expected_rows)

        # Assert correct number of imports
        expected_imports = len(expected_df)
        error_msg = f"Expected {expected_imports} imports but got {imported_count}"
        assert imported_count == expected_imports, error_msg

        # Compare with DB contents
        verify_db_contents(expected_df)


def test_import_transactions_ignore_columns(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test importing transactions with header_ignore functionality.

    This test verifies that:
    1. Columns in header_ignore are ignored during import
    2. Essential columns are never ignored, even if in header_ignore list
    3. Non-essential columns can be successfully ignored
    """
    with temp_config(
        header_ignore=[
            "IgnoreMe",
            "AlsoIgnore",
            "TxnDate",  # TxnDate should not be ignored
        ],
    ) as ctx:
        config = ctx.config
        ensure_folio_exists()

        txn_sheet = config.transactions_sheet()
        temp_path = config.folio_path.parent / "temp_ignore_columns.xlsx"

        test_data = {
            Column.Txn.TXN_DATE: [
                "2025-02-05T20:29:41.785270Z",
                "2025-02-07 00:00:00",
                "2025-02-08",
            ],
            Column.Txn.ACTION: [
                "BUY",
                "DIVIDEND",
                "CONTRIBUTION",
            ],
            Column.Txn.AMOUNT: [1000.0, 50.0, 2000.0],
            Column.Txn.CURRENCY: ["USD", "USD", "CAD"],
            Column.Txn.PRICE: [100.0, 0.0, 200.0],
            Column.Txn.UNITS: [10.0, 0.0, 10.0],
            Column.Txn.TICKER: ["AAPL", "AAPL", "SHOP"],
            Column.Txn.ACCOUNT: ["TEST-ACCOUNT", "TEST-ACCOUNT", "TEST-ACCOUNT"],
            "IgnoreMe": ["This", "Should", "Not"],  # Should be ignored
            "AlsoIgnore": ["Be", "In", "DB"],  # Should be ignored
            "KeepThis": ["But", "This", "Should"],  # Should be kept
        }

        df = pd.DataFrame(test_data)
        df.to_excel(temp_path, index=False, sheet_name=txn_sheet)
        config.db_path.unlink(missing_ok=True)
        import_transactions(temp_path, None, txn_sheet)
        expected_df = pd.DataFrame(
            {
                Column.Txn.TXN_DATE: ["2025-02-05", "2025-02-07", "2025-02-08"],
                Column.Txn.ACTION: ["BUY", "DIVIDEND", "CONTRIBUTION"],
                Column.Txn.AMOUNT: [1000.0, 50.0, 2000.0],
                Column.Txn.CURRENCY: ["USD", "USD", "CAD"],
                Column.Txn.PRICE: [100.0, 0.0, 200.0],
                Column.Txn.UNITS: [10.0, 0.0, 10.0],
                Column.Txn.TICKER: ["AAPL", "AAPL", "SHOP"],
                Column.Txn.ACCOUNT: [
                    "TEST-ACCOUNT",
                    "TEST-ACCOUNT",
                    "TEST-ACCOUNT",
                ],
                "KeepThis": ["But", "This", "Should"],
            },
        )
        verify_db_contents(expected_df)


def test_import_account_fallback(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test importing transactions with account fallback when Account column is missing.

    This test verifies that:
    1. When Account column is completely absent, the fallback account parameter is used
    2. All rows get populated with the fallback account value
    3. Transactions can be imported successfully without Account column present
    """
    with temp_config() as ctx:
        config = ctx.config
        ensure_folio_exists()

        txn_sheet = config.transactions_sheet()
        temp_path = config.folio_path.parent / "temp_account_fallback.xlsx"

        # Create test data WITHOUT Account column
        test_data = {
            Column.Txn.TXN_DATE: [
                "2025-03-01",
                "2025-03-02",
                "2025-03-03",
            ],
            Column.Txn.ACTION: [
                "BUY",
                "SELL",
                "DIVIDEND",
            ],
            Column.Txn.AMOUNT: [1000.0, 2000.0, 500.0],
            Column.Txn.CURRENCY: ["USD", "USD", "USD"],
            Column.Txn.PRICE: [100.0, 200.0, 0.0],
            Column.Txn.UNITS: [10.0, 10.0, 0.0],
            Column.Txn.TICKER: ["AAPL", "MSFT", "AAPL"],
            # Note: NO Account column in this test data
        }

        df = pd.DataFrame(test_data)
        df.to_excel(temp_path, index=False, sheet_name=txn_sheet)
        config.db_path.unlink(missing_ok=True)

        # Import with account fallback
        fallback_account = "FALLBACK-ACCOUNT"
        import_transactions(temp_path, fallback_account, txn_sheet)
        expected_df = pd.DataFrame(
            {
                Column.Txn.TXN_DATE: ["2025-03-01", "2025-03-02", "2025-03-03"],
                Column.Txn.ACTION: ["BUY", "SELL", "DIVIDEND"],
                Column.Txn.AMOUNT: [1000.0, 2000.0, 500.0],
                Column.Txn.CURRENCY: ["USD", "USD", "USD"],
                Column.Txn.PRICE: [100.0, 200.0, 0.0],
                Column.Txn.UNITS: [10.0, 10.0, 0.0],
                Column.Txn.TICKER: ["AAPL", "MSFT", "AAPL"],
                Column.Txn.ACCOUNT: [fallback_account] * 3,
            },
        )
        verify_db_contents(expected_df)


def test_import_account_missing(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test that import fails when Account column is missing and no fallback."""
    with temp_config() as ctx:
        config = ctx.config
        ensure_folio_exists()

        txn_sheet = config.transactions_sheet()
        temp_path = config.folio_path.parent / "temp_account_missing.xlsx"

        # Create test data WITHOUT Account column
        test_data = {
            Column.Txn.TXN_DATE: ["2025-03-01"],
            Column.Txn.ACTION: ["BUY"],
            Column.Txn.AMOUNT: [1000.0],
            Column.Txn.CURRENCY: ["USD"],
            Column.Txn.PRICE: [100.0],
            Column.Txn.UNITS: [10.0],
            Column.Txn.TICKER: ["AAPL"],
            # Note: NO Account column in this test data
        }

        df = pd.DataFrame(test_data)
        df.to_excel(temp_path, index=False, sheet_name=txn_sheet)
        config.db_path.unlink(missing_ok=True)

        # Import WITHOUT account fallback should fail
        with pytest.raises(
            ValueError,
            match=r"MISSING essential columns: \{'Account'\}",
        ):
            import_transactions(temp_path, None, txn_sheet)  # No account parameter


def test_import_action_validation(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test end-to-end import with action-specific validation rules."""
    with temp_config() as ctx:
        config = ctx.config
        ensure_folio_exists()

        test_data = {
            Column.Txn.TXN_DATE: [
                "2023-05-17",  # FCH - should import (no Ticker required)
                "2023-08-02",  # CONTRIBUTION - should import (no Ticker required)
                "2023-09-08",  # DIVIDEND - should REJECT (missing required Ticker)
                "2023-01-01",  # BUY - should import (has all required fields)
                "2023-10-10",  # ROC - should REJECT (missing required Ticker)
            ],
            Column.Txn.ACTION: ["FCH", "CONTRIBUTION", "DIVIDEND", "BUY", "ROC"],
            Column.Txn.AMOUNT: [0.5, 500.0, 0.87, 1000.0, 500.0],
            Column.Txn.CURRENCY: ["CAD", "CAD", "USD", "USD", "CAD"],
            Column.Txn.PRICE: [pd.NA, pd.NA, pd.NA, 100.0, pd.NA],
            Column.Txn.UNITS: [pd.NA, pd.NA, pd.NA, 10.0, pd.NA],
            Column.Txn.TICKER: [pd.NA, pd.NA, pd.NA, "AAPL", pd.NA],
            Column.Txn.ACCOUNT: ["TEST-ACCOUNT"] * 5,
        }

        # Create Excel file with test data
        df = pd.DataFrame(test_data)
        txn_sheet = config.transactions_sheet()
        temp_path = config.folio_path.parent / "test_integration_scenarios.xlsx"
        df.to_excel(temp_path, index=False, sheet_name=txn_sheet)

        config.db_path.unlink(missing_ok=True)
        import_transactions(temp_path, "TEST-ACCOUNT", txn_sheet)

        # Only expect 3 transactions to be imported (FCH, CONTRIBUTION, BUY)
        # DIVIDEND and ROC should be rejected for missing required Ticker
        expected_df = pd.DataFrame(
            {
                Column.Txn.TXN_DATE: [
                    "2023-05-17",  # FCH
                    "2023-08-02",  # CONTRIBUTION
                    "2023-01-01",  # BUY
                ],
                Column.Txn.ACTION: [
                    "FCH",
                    "CONTRIBUTION",
                    "BUY",
                ],
                Column.Txn.AMOUNT: [0.5, 500.0, 1000.0],
                Column.Txn.CURRENCY: ["CAD", "CAD", "USD"],
                Column.Txn.PRICE: [pd.NA, pd.NA, 100.0],
                Column.Txn.UNITS: [pd.NA, pd.NA, 10.0],
                Column.Txn.TICKER: [pd.NA, pd.NA, "AAPL"],
                Column.Txn.ACCOUNT: ["TEST-ACCOUNT"] * 3,
            },
        )
        verify_db_contents(expected_df)


def test_import_transactions_db_duplicate_approval(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    with temp_config() as ctx:
        config = ctx.config
        ensure_folio_exists()

        # First, import some initial transactions
        initial_data = {
            Column.Txn.TXN_DATE: ["2024-01-01", "2024-01-02"],
            Column.Txn.ACTION: ["BUY", "SELL"],
            Column.Txn.AMOUNT: [1000.0, 2000.0],
            Column.Txn.CURRENCY: ["USD", "USD"],
            Column.Txn.PRICE: [100.0, 200.0],
            Column.Txn.UNITS: [10.0, 10.0],
            Column.Txn.TICKER: ["AAPL", "MSFT"],
            Column.Txn.ACCOUNT: ["TEST-ACCOUNT", "TEST-ACCOUNT"],
        }

        initial_df = pd.DataFrame(initial_data)
        txn_sheet = config.transactions_sheet()
        initial_path = config.folio_path.parent / "initial_transactions.xlsx"
        initial_df.to_excel(initial_path, index=False, sheet_name=txn_sheet)
        config.db_path.unlink(missing_ok=True)
        initial_count = import_transactions(initial_path, "TEST-ACCOUNT", txn_sheet)
        expected_initial_count = 2
        assert initial_count == expected_initial_count

        # Now try to import duplicates without approval - should be rejected
        duplicate_data: dict[str, Any] = {
            # First is duplicate
            Column.Txn.TXN_DATE: ["2024-01-01", "2024-01-03"],
            Column.Txn.ACTION: ["BUY", "DIVIDEND"],
            Column.Txn.AMOUNT: [1000.0, 500.0],
            Column.Txn.CURRENCY: ["USD", "USD"],
            Column.Txn.PRICE: [100.0, 0.0],
            Column.Txn.UNITS: [10.0, 0.0],
            Column.Txn.TICKER: ["AAPL", "AAPL"],
            Column.Txn.ACCOUNT: ["TEST-ACCOUNT", "TEST-ACCOUNT"],
        }

        duplicate_df = pd.DataFrame(duplicate_data)
        duplicate_path = config.folio_path.parent / "duplicate_transactions.xlsx"
        duplicate_df.to_excel(duplicate_path, index=False, sheet_name=txn_sheet)
        no_approval_count = import_transactions(
            duplicate_path,
            "TEST-ACCOUNT",
            txn_sheet,
        )
        expected_no_approval_count = 1  # Only the DIVIDEND transaction
        assert no_approval_count == expected_no_approval_count

        # Now add approval column and try again
        duplicate_data_with_approval = duplicate_data.copy()
        duplicate_data_with_approval[config.duplicate_approval_column] = ["OK", ""]
        approved_df = pd.DataFrame(duplicate_data_with_approval)
        approved_path = config.folio_path.parent / "approved_duplicates.xlsx"
        approved_df.to_excel(approved_path, index=False, sheet_name=txn_sheet)
        approved_count = import_transactions(approved_path, "TEST-ACCOUNT", txn_sheet)
        expected_approved_count = 1  # The approved duplicate BUY transaction
        assert approved_count == expected_approved_count

        # Verify final database contents
        expected_data = {
            Column.Txn.TXN_DATE: [
                "2024-01-01",
                "2024-01-02",
                "2024-01-03",
                "2024-01-01",
            ],
            Column.Txn.ACTION: ["BUY", "SELL", "DIVIDEND", "BUY"],
            Column.Txn.AMOUNT: [1000.0, 2000.0, 500.0, 1000.0],
            Column.Txn.CURRENCY: ["USD", "USD", "USD", "USD"],
            Column.Txn.PRICE: [100.0, 200.0, 0.0, 100.0],
            Column.Txn.UNITS: [10.0, 10.0, 0.0, 10.0],
            Column.Txn.TICKER: ["AAPL", "MSFT", "AAPL", "AAPL"],
            Column.Txn.ACCOUNT: ["TEST-ACCOUNT"] * 4,
        }
        expected_df = pd.DataFrame(expected_data)
        verify_db_contents(expected_df)


def test_import_transactions_intra_duplicate_approval(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    with temp_config() as ctx:
        config = ctx.config
        ensure_folio_exists()

        # Create test data with intra-import duplicates
        approval_column = config.duplicate_approval_column
        # First two are duplicates, second has approval
        test_data = {
            Column.Txn.TXN_DATE: ["2024-02-01", "2024-02-01", "2024-02-02"],
            Column.Txn.ACTION: ["BUY", "BUY", "SELL"],
            Column.Txn.AMOUNT: [1000.0, 1000.0, 2000.0],
            Column.Txn.CURRENCY: ["USD", "USD", "USD"],
            Column.Txn.PRICE: [100.0, 100.0, 200.0],
            Column.Txn.UNITS: [10.0, 10.0, 10.0],
            Column.Txn.TICKER: ["AAPL", "AAPL", "MSFT"],
            Column.Txn.ACCOUNT: ["TEST-ACCOUNT"] * 3,
            approval_column: ["", "OK", ""],
        }

        df = pd.DataFrame(test_data)
        txn_sheet = config.transactions_sheet()
        temp_path = config.folio_path.parent / "intra_duplicates_with_approval.xlsx"
        df.to_excel(temp_path, index=False, sheet_name=txn_sheet)
        config.db_path.unlink(missing_ok=True)
        imported_count = import_transactions(temp_path, "TEST-ACCOUNT", txn_sheet)
        expected_imported_count = 2
        assert imported_count == expected_imported_count

        # Verify database contents
        expected_data = {
            Column.Txn.TXN_DATE: ["2024-02-01", "2024-02-02"],
            Column.Txn.ACTION: ["BUY", "SELL"],
            Column.Txn.AMOUNT: [1000.0, 2000.0],
            Column.Txn.CURRENCY: ["USD", "USD"],
            Column.Txn.PRICE: [100.0, 200.0],
            Column.Txn.UNITS: [10.0, 10.0],
            Column.Txn.TICKER: ["AAPL", "MSFT"],
            Column.Txn.ACCOUNT: ["TEST-ACCOUNT"] * 2,
        }
        expected_df = pd.DataFrame(expected_data)
        verify_db_contents(expected_df)


def test_import_transactions_optional_fields(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test importing transactions with configured optional fields.

    This test verifies that:
    1. Optional fields are formatted according to their configured data types
    2. Invalid values retain their original values and don't cause import issues
    3. Missing optional fields don't prevent successful import
    4. All data types (date, numeric, currency, action, string) are handled
    """
    config_yaml = """
Fees:
    keywords: ["Fees"]
    type: numeric
SettleDate:
    keywords: ["Custom Date"]
    type: date
TradeCurrency:
    keywords: ["Trade Currency"]
    type: currency
Side:
    keywords: ["Side"]
    type: action
Notes:
    keywords: ["Notes"]
    type: string
"""
    optional_config = yaml.safe_load(config_yaml)
    with temp_config(optional_columns=optional_config) as ctx:
        config = ctx.config
        ensure_folio_exists()

        txn_sheet = config.transactions_sheet()
        temp_path = config.folio_path.parent / "temp_optional_fields.xlsx"
        test_data = {
            # Essential fields - all valid
            Column.Txn.TXN_DATE: [
                "2023-01-01",
                "2023-01-02",
                "2023-01-03",
                "2023-01-04",
                "2023-01-05",
            ],
            Column.Txn.ACTION: ["BUY", "SELL", "DIVIDEND", "BUY", "SELL"],
            Column.Txn.AMOUNT: [1000.0, 2000.0, 150.0, 1500.0, 800.0],
            Column.Txn.CURRENCY: ["USD", "USD", "USD", "USD", "USD"],
            Column.Txn.PRICE: [100.0, 200.0, 15.0, 150.0, 80.0],
            Column.Txn.UNITS: [10.0, 10.0, 10.0, 10.0, 10.0],
            Column.Txn.TICKER: ["AAPL", "MSFT", "AAPL", "GOOGL", "TSLA"],
            Column.Txn.ACCOUNT: ["TEST-ACCOUNT"] * 5,
            # Optional fields with various formats and validity
            "Fees": [
                "$5.95",  # Valid numeric with formatting -> 5.95
                "INVALID",  # Invalid numeric -> retains "INVALID"
                "",  # Empty -> NULL
                "10.50",  # Valid numeric -> 10.50
                pd.NA,  # Already null -> NULL
            ],
            "Custom Date": [
                "01/03/2023",  # Valid date with formatting -> 2023-01-03
                "INVALID_DATE",  # Invalid date -> retains "INVALID_DATE"
                "2023-01-05",  # Already formatted -> 2023-01-05
                "",  # Empty -> NULL
                "2023-01-07T10:30:00Z",  # ISO format -> 2023-01-07
            ],
            "Trade Currency": [
                "US$",  # Valid currency with formatting -> USD
                "INVALID_CURR",  # Invalid currency -> retains "INVALID_CURR"
                "CAD",  # Valid currency -> CAD
                "",  # Empty -> NULL
                pd.NA,  # Already null -> NULL
            ],
            "Side": [
                "B",  # Valid action with formatting -> BUY
                "INVALID_ACTION",  # Invalid action -> retains "INVALID_ACTION"
                "SELL",  # Valid action -> SELL
                "",  # Empty -> NULL
                "DIV",  # Valid action with formatting -> DIVIDEND
            ],
            "Notes": [
                "  Some note  ",  # String with whitespace -> "Some note"
                "Regular note",  # Normal string -> "Regular note"
                "",  # Empty -> NULL
                "Another note",  # Normal string -> "Another note"
                pd.NA,  # Already null -> NULL
            ],
        }

        df = pd.DataFrame(test_data)
        df.to_excel(temp_path, index=False, sheet_name=txn_sheet)
        config.db_path.unlink(missing_ok=True)
        imported_count = import_transactions(temp_path, txn_sheet)

        # All rows should import successfully (optional field issues don't reject rows)
        expected_import_count = 5
        assert imported_count == expected_import_count

        # Create expected DataFrame with properly formatted values
        expected_df = pd.DataFrame(
            {
                Column.Txn.TXN_DATE: [
                    "2023-01-01",
                    "2023-01-02",
                    "2023-01-03",
                    "2023-01-04",
                    "2023-01-05",
                ],
                Column.Txn.ACTION: ["BUY", "SELL", "DIVIDEND", "BUY", "SELL"],
                Column.Txn.AMOUNT: [1000.0, 2000.0, 150.0, 1500.0, 800.0],
                Column.Txn.CURRENCY: ["USD", "USD", "USD", "USD", "USD"],
                Column.Txn.PRICE: [100.0, 200.0, 15.0, 150.0, 80.0],
                Column.Txn.UNITS: [10.0, 10.0, 10.0, 10.0, 10.0],
                Column.Txn.TICKER: ["AAPL", "MSFT", "AAPL", "GOOGL", "TSLA"],
                Column.Txn.ACCOUNT: ["TEST-ACCOUNT"] * 5,
                # Optional fields: valid values formatted, invalid values retained
                # When read from database, mixed columns normalize to consistent types
                "Fees": ["5.95", "INVALID", None, "10.50", None],
                "CustomDate": [
                    "2023-01-03",
                    "INVALID_DATE",
                    "2023-01-05",
                    None,
                    "2023-01-07",
                ],
                "TradeCurrency": ["USD", "INVALID_CURR", "CAD", None, None],
                "Side": ["BUY", "INVALID_ACTION", "SELL", None, "DIVIDEND"],
                "Notes": ["Some note", "Regular note", None, "Another note", None],
            },
        )

        verify_db_contents(expected_df)


def test_import_transactions_no_optional_fields_required(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test that rows import successfully when optional fields are missing."""
    optional_config = {
        "Fees": "numeric",
        "Notes": "string",
    }

    with temp_config(optional_headers=optional_config) as ctx:
        config = ctx.config
        ensure_folio_exists()

        txn_sheet = config.transactions_sheet()
        temp_path = config.folio_path.parent / "temp_no_optional_fields.xlsx"

        # Test data with only essential fields (no optional fields)
        test_data = {
            Column.Txn.TXN_DATE: ["2023-01-01"],
            Column.Txn.ACTION: ["BUY"],
            Column.Txn.AMOUNT: [1000.0],
            Column.Txn.CURRENCY: ["USD"],
            Column.Txn.PRICE: [100.0],
            Column.Txn.UNITS: [10.0],
            Column.Txn.TICKER: ["AAPL"],
            Column.Txn.ACCOUNT: ["TEST-ACCOUNT"],
        }

        df = pd.DataFrame(test_data)
        df.to_excel(temp_path, index=False, sheet_name=txn_sheet)
        config.db_path.unlink(missing_ok=True)
        imported_count = import_transactions(temp_path, "TEST-ACCOUNT", txn_sheet)
        assert imported_count == 1

        expected_df = pd.DataFrame(
            {
                Column.Txn.TXN_DATE: ["2023-01-01"],
                Column.Txn.ACTION: ["BUY"],
                Column.Txn.AMOUNT: [1000.0],
                Column.Txn.CURRENCY: ["USD"],
                Column.Txn.PRICE: [100.0],
                Column.Txn.UNITS: [10.0],
                Column.Txn.TICKER: ["AAPL"],
                Column.Txn.ACCOUNT: ["TEST-ACCOUNT"],
            },
        )

        verify_db_contents(expected_df)


def _get_default_dataframe(config: Config) -> pd.DataFrame:
    """Get the default DataFrame from the transactions sheet."""
    txn_sheet = config.transactions_sheet()
    return pd.read_excel(config.folio_path, sheet_name=txn_sheet)


def _test_duplicate_import(config: Config, default_df: pd.DataFrame) -> None:
    """Test importing duplicate transactions results in 0 new imports."""
    txn_sheet = config.transactions_sheet()
    transactions = import_transactions(config.folio_path, "TEST-ACCOUNT", txn_sheet)
    assert transactions == 0
    verify_db_contents(default_df, last_n=len(default_df))


def _test_empty_db_import(config: Config, default_df: pd.DataFrame) -> None:
    """Test importing into an empty database."""
    config.db_path.unlink()
    txn_sheet = config.transactions_sheet()
    assert import_transactions(config.folio_path, "TEST-ACCOUNT", txn_sheet) > 0
    verify_db_contents(default_df, last_n=len(default_df))


def _test_missing_essential_column(config: Config, default_df: pd.DataFrame) -> None:
    """Test importing with missing essential column should raise ValueError."""
    df = default_df.copy()
    essential_to_remove = next(iter(TXN_ESSENTIALS))

    df = df.drop(columns=[essential_to_remove])
    txn_sheet = config.transactions_sheet()
    temp_path = config.folio_path.parent / "temp_missing_essential.xlsx"
    df.to_excel(temp_path, index=False, sheet_name=txn_sheet)

    with pytest.raises(
        ValueError,
        match=rf"MISSING essential columns: \{{'{essential_to_remove}'\}}\s*",
    ):
        import_transactions(temp_path, None, txn_sheet)


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
    if "Ticker" in df.columns:
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
    assert import_transactions(temp_path, None, txn_sheet) > 0
    verify_db_contents(df, last_n=len(df))
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
    df_scrambled = df[cols]

    txn_sheet = config.transactions_sheet()
    temp_path = config.folio_path.parent / "temp_scrambled_columns.xlsx"
    df_scrambled.to_excel(temp_path, index=False, sheet_name=txn_sheet)
    assert import_transactions(temp_path, None, txn_sheet) > 0

    # The database should store columns in the proper order (TXN_ESSENTIALS first)
    # So we compare against the original ordered DataFrame, not the scrambled one
    verify_db_contents(df, last_n=len(df))


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
    assert import_transactions(temp_path, None, txn_sheet) > 0

    # Pad df with missing columns that exist in the database
    with get_connection() as conn:
        query = f'SELECT * FROM "{Table.TXNS}"'
        table_df = pd.read_sql_query(query, conn)
        for col in table_df.columns:
            if col not in df.columns:
                df[col] = pd.NA
        df = df[table_df.columns]  # Reorder columns to match DB

    verify_db_contents(df, last_n=len(df))


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
