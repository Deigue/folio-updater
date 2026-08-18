"""Optimized tests for excel_importer module."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pandas as pd
import pytest

from datagen import ensure_data_exists
from db import create_txns_table, get_connection, get_rows
from importers import import_statements, import_transactions
from utils.constants import TXN_ESSENTIALS, Column, Table

from .fixtures.dataframe_cache import register_test_dataframe
from .helpers.dataframe import verify_db_contents

# Import assertions read the exported parquet back, so these need the real one.
pytestmark = pytest.mark.real_parquet_export

if TYPE_CHECKING:
    from utils.config import Config

    from .test_types import TempContext

logger: logging.Logger = logging.getLogger(__name__)

pd.set_option("display.max_columns", None)
pd.set_option("display.width", 200)
pd.set_option("display.max_colwidth", None)


@pytest.mark.parametrize(
    (
        "scenario",
        "test_data",
        "expected_count",
        "expected_rows",
        "config_overrides",
    ),
    [
        # Mega formatting test covering all validation paths
        (
            "formatting_validation",
            {
                Column.Txn.TXN_DATE: [
                    "2023-01-01",  # 0: Good case - all columns perfect
                    "01/02/2023",  # 1: Auto-formatted date (MM/DD/YYYY -> YYYY-MM-DD)
                    "2023-01-03T10:30:45Z",  # 2: ISO 8601 format with timezone
                    "INVALID_DATE",  # 3: Invalid date - should be rejected
                    "",  # 4: Empty date - should be rejected
                    "2023-01-05 15:45:30",  # 5: Datetime format with space
                    pd.NA,  # 6: Invalid action - should be rejected
                    "2023-01-07",  # 7: Action abbreviation (will be normalized)
                    "2023-01-08",  # 8: Empty amount - should be rejected
                    "2023-01-09",  # 9: Invalid amount format - should be rejected
                    "2023-01-10",  # 10: Invalid currency - should be rejected
                    "2023-01-11",  # 11: Missing currency - should be rejected
                    "2023-01-12T20:15:30.123456Z",  # 12: ISO format with ms
                    "2023-01-13",  # 13: Empty price - should be rejected
                    "2023-01-14",  # 14: Invalid price format - should be rejected
                    "2023-01-15",  # 15: Empty units - should be rejected
                    "2023-01-16",  # 16: Invalid units format - should be rejected
                    "2023-01-17",  # 17: Empty ticker (valid - becomes NULL)
                    "2023-01-18",  # 18: NULL ticker (valid - stays NULL)
                    "2023-01-19",  # 19: Invalid ticker format - should be rejected
                    # 20: Multiple invalid: empty amount, invalid price/units
                    "2023-01-20",
                    # 21: Multiple invalid: no currency, bad ticker/action
                    "2023-01-21",
                ],
                Column.Txn.ACTION: [
                    "BUY",
                    "SELL",
                    "DIVIDEND",
                    "BUY",
                    "BUY",
                    None,
                    "INVALID_ACTION",
                    "DIV",  # Abbreviation -> DIVIDEND
                    "BUY",
                    "BUY",
                    "SELL",
                    "SELL",
                    "CONTRIBUTION",
                    "BUY",
                    "BUY",
                    "SELL",
                    "SELL",
                    "WITHDRAWAL",
                    "CONTRIBUTION",
                    "BUY",
                    "BUY",
                    "INVALID_ACTION",
                ],
                Column.Txn.AMOUNT: [
                    1000.0,
                    2000.0,
                    1500.0,
                    1000.0,
                    1000.0,
                    1000.0,
                    1000.0,
                    "$1,000.00",  # Formatted -> 1000.00
                    "",
                    "INVALID_AMOUNT",
                    1000.0,
                    1000.0,
                    1000.0,
                    1000.0,
                    1000.0,
                    1000.0,
                    1000.0,
                    1000.0,
                    1000.0,
                    1000.0,
                    "",
                    1000.0,
                ],
                Column.Txn.CURRENCY: [
                    "USD",
                    "USD",
                    "USD",
                    "USD",
                    "USD",
                    "USD",
                    "USD",
                    "USD",
                    "USD",
                    "USD",
                    "INVALID_CURRENCY",
                    None,
                    "US$",  # Alternative format -> USD
                    "USD",
                    "USD",
                    "USD",
                    "USD",
                    "USD",
                    "USD",
                    "USD",
                    "USD",
                    None,
                ],
                Column.Txn.PRICE: [
                    100.0,
                    200.0,
                    150.0,
                    100.0,
                    100.0,
                    100.0,
                    100.0,
                    100.0,
                    100.0,
                    100.0,
                    100.0,
                    100.0,
                    100.0,
                    "",
                    "INVALID_PRICE",
                    100.0,
                    100.0,
                    100.0,
                    100.0,
                    100.0,
                    "INVALID_PRICE",
                    100.0,
                ],
                Column.Txn.UNITS: [
                    10.0,
                    10.0,
                    10.0,
                    10.0,
                    10.0,
                    10.0,
                    10.0,
                    10.0,
                    10.0,
                    10.0,
                    10.0,
                    10.0,
                    10.0,
                    10.0,
                    10.0,
                    "",
                    "INVALID_UNITS",
                    10.0,
                    10.0,
                    10.0,
                    "INVALID_UNITS",
                    10.0,
                ],
                Column.Txn.TICKER: [
                    "AAPL",
                    "MSFT",
                    "aapl",  # Lowercase -> AAPL
                    "GOOG",
                    "AAPL",
                    "TSLA",
                    "AMZN",
                    "NFLX",
                    "META",
                    "NVDA",
                    "ADBE",
                    "PYPL",
                    "PYPL",
                    "CSCO",
                    "INTC",
                    "CMCSA",
                    "PEP",
                    "",  # Empty -> NULL
                    None,  # NULL -> NULL
                    "INVALID@TICKER",
                    "AAPL",
                    "INVALID@TICKER",
                ],
            },
            7,  # Only rows 0,1,2,7,12,17,18 are valid
            [0, 1, 2, 7, 12, 17, 18],
            {},
        ),
        # Optional fields test - covers all 5 field types
        (
            "optional_fields",
            {
                Column.Txn.TXN_DATE: [
                    "2023-02-01",
                    "2023-02-02",
                    "2023-02-03",
                    "2023-02-04",
                    "2023-02-05",
                    "2023-02-06",
                ],
                Column.Txn.ACTION: ["BUY", "SELL", "DIVIDEND", "BUY", "SELL", "BUY"],
                Column.Txn.AMOUNT: [1000.0, 2000.0, 150.0, 1500.0, 800.0, 900.0],
                Column.Txn.CURRENCY: ["USD"] * 6,
                Column.Txn.PRICE: [100.0, 200.0, 15.0, 150.0, 80.0, 90.0],
                Column.Txn.UNITS: [10.0, 10.0, 10.0, 10.0, 10.0, 10.0],
                Column.Txn.TICKER: ["AAPL", "MSFT", "AAPL", "GOOGL", "TSLA", "NVDA"],
                Column.Txn.ACCOUNT: ["TEST-ACCOUNT"] * 6,
                "Fees": ["$5.95", "6.95", "", "10.50", pd.NA, "INVALID"],  # numeric
                "Custom Date": [
                    "01/03/2023",
                    "INVALID_DATE",
                    pd.NA,
                    "",
                    "2023-02-07T10:30:00Z",
                    pd.NA,
                ],  # date
                "Trade Currency": [
                    "US$",
                    "INVALID_CURR",
                    "CAD",
                    "",
                    pd.NA,
                    pd.NA,
                ],  # currency
                "Side": ["B", "INVALID_ACTION", "SELL", "", "DIV", pd.NA],  # action
                "Notes": [
                    "  Some note  ",
                    "Regular note",
                    "",
                    "Another note",
                    pd.NA,
                    pd.NA,
                ],  # string
            },
            5,
            [0, 1, 2, 3, 4],  # Row 5 rejected due to invalid numeric.
            {
                "optional_columns": {
                    "Fees": {"keywords": ["Fees"], "type": "numeric"},
                    "SettleDate": {"keywords": ["Custom Date"], "type": "date"},
                    "TradeCurrency": {
                        "keywords": ["Trade Currency"],
                        "type": "currency",
                    },
                    "Side": {"keywords": ["Side"], "type": "action"},
                    "Notes": {"keywords": ["Notes"], "type": "string"},
                },
            },
        ),
        # Action validation test
        (
            "action_validation",
            {
                Column.Txn.TXN_DATE: [
                    "2023-05-17",
                    "2023-08-02",
                    "2023-09-08",
                    "2023-01-01",
                    "2023-10-10",
                ],
                Column.Txn.ACTION: ["FCH", "CONTRIBUTION", "DIVIDEND", "BUY", "ROC"],
                Column.Txn.AMOUNT: [0.5, 500.0, 0.87, 1000.0, 500.0],
                Column.Txn.CURRENCY: ["CAD", "CAD", "USD", "USD", "CAD"],
                Column.Txn.PRICE: [pd.NA, pd.NA, pd.NA, 100.0, pd.NA],
                Column.Txn.UNITS: [pd.NA, pd.NA, pd.NA, 10.0, pd.NA],
                Column.Txn.TICKER: [pd.NA, pd.NA, pd.NA, "AAPL", pd.NA],
                Column.Txn.ACCOUNT: ["TEST-ACCOUNT"] * 5,
            },
            3,  # FCH, CONTRIBUTION, BUY valid; DIVIDEND and ROC missing Ticker
            [0, 1, 3],
            {},
        ),
        # Ignore columns test
        (
            "ignore_columns",
            {
                Column.Txn.TXN_DATE: [
                    "2025-02-05T20:29:41.785270Z",
                    "2025-02-07 00:00:00",
                    "2025-02-08",
                ],
                Column.Txn.ACTION: ["BUY", "DIVIDEND", "CONTRIBUTION"],
                Column.Txn.AMOUNT: [1000.0, 50.0, 2000.0],
                Column.Txn.CURRENCY: ["USD", "USD", "CAD"],
                Column.Txn.PRICE: [100.0, 0.0, 200.0],
                Column.Txn.UNITS: [10.0, 0.0, 10.0],
                Column.Txn.TICKER: ["AAPL", "AAPL", "SHOP"],
                Column.Txn.ACCOUNT: ["TEST-ACCOUNT"] * 3,
                "IgnoreMe": ["This", "Should", "Not"],
                "AlsoIgnore": ["Be", "In", "DB"],
                "KeepThis": ["But", "This", "Should"],
            },
            3,
            [0, 1, 2],
            {"header_ignore": ["IgnoreMe", "AlsoIgnore", "TxnDate"]},
        ),
        # Account fallback test
        (
            "account_fallback",
            {
                Column.Txn.TXN_DATE: ["2025-03-01", "2025-03-02", "2025-03-03"],
                Column.Txn.ACTION: ["BUY", "SELL", "DIVIDEND"],
                Column.Txn.AMOUNT: [1000.0, 2000.0, 500.0],
                Column.Txn.CURRENCY: ["USD", "USD", "USD"],
                Column.Txn.PRICE: [100.0, 200.0, 0.0],
                Column.Txn.UNITS: [10.0, 10.0, 0.0],
                Column.Txn.TICKER: ["AAPL", "MSFT", "AAPL"],
                # NO Account column
            },
            3,
            [0, 1, 2],
            {},
        ),
    ],
)
def test_import_scenarios(  # noqa: PLR0915
    temp_ctx: TempContext,
    scenario: str,
    test_data: dict[str, Any],
    expected_count: int,
    expected_rows: list[int],
    config_overrides: dict[str, Any],
) -> None:
    """Mega test covering all formatting, validation, and optional field scenarios."""
    with temp_ctx(**config_overrides) as ctx:
        config = ctx.config
        temp_path = config.folio_path.parent / f"test_{scenario}.xlsx"
        df = pd.DataFrame(test_data)
        register_test_dataframe(temp_path, df)

        # Clear database
        config.db_path.unlink(missing_ok=True)

        # Import transactions
        account_fallback = (
            "FALLBACK-ACCOUNT" if scenario == "account_fallback" else "TEST-ACCOUNT"
        )
        imported_count = import_transactions(temp_path, account_fallback)

        # Verify count
        assert imported_count == expected_count, (
            f"Expected {expected_count} imports but got {imported_count}"
        )

        # Create expected DataFrame with only valid rows
        expected_df = df.iloc[expected_rows].copy()

        # Handle special cases for expected data
        if scenario == "formatting_validation":
            # Update expected values based on formatting rules
            expected_df.loc[expected_df.index[0], Column.Txn.TICKER] = "AAPL"
            expected_df.loc[expected_df.index[1], Column.Txn.TXN_DATE] = "2023-01-02"
            expected_df.loc[expected_df.index[2], Column.Txn.TXN_DATE] = "2023-01-03"
            expected_df.loc[expected_df.index[2], Column.Txn.TICKER] = "AAPL"
            expected_df.loc[expected_df.index[3], Column.Txn.TXN_DATE] = "2023-01-07"
            expected_df.loc[expected_df.index[3], Column.Txn.ACTION] = "DIVIDEND"
            expected_df.loc[expected_df.index[3], Column.Txn.AMOUNT] = 1000.0
            expected_df.loc[expected_df.index[4], Column.Txn.TXN_DATE] = "2023-01-12"
            expected_df.loc[expected_df.index[4], Column.Txn.CURRENCY] = "USD"
            expected_df.loc[expected_df.index[5], Column.Txn.TXN_DATE] = "2023-01-17"
            expected_df.loc[expected_df.index[5], Column.Txn.TICKER] = pd.NA
            expected_df.loc[expected_df.index[6], Column.Txn.TXN_DATE] = "2023-01-18"
            expected_df.loc[expected_df.index[6], Column.Txn.TICKER] = pd.NA
            expected_df[Column.Txn.ACCOUNT] = "TEST-ACCOUNT"

        elif scenario == "optional_fields":
            # Update expected optional field values based on formatting
            expected_df[Column.Txn.ACCOUNT] = "TEST-ACCOUNT"
            # Update formatted optional field values
            expected_df.loc[expected_df.index[0], "Fees"] = "5.95"  # $5.95 -> 5.95
            expected_df.loc[expected_df.index[2], "Fees"] = pd.NA  # "" -> NULL
            expected_df.loc[expected_df.index[0], "Custom Date"] = "2023-01-03"
            expected_df.loc[expected_df.index[2], "Custom Date"] = "2023-02-05"
            expected_df.loc[expected_df.index[3], "Custom Date"] = pd.NA
            expected_df.loc[expected_df.index[4], "Custom Date"] = "2023-02-07"
            expected_df.loc[expected_df.index[0], "Trade Currency"] = "USD"  # US$
            expected_df.loc[expected_df.index[3], "Trade Currency"] = pd.NA
            expected_df.loc[expected_df.index[0], "Side"] = "BUY"  # B -> BUY
            expected_df.loc[expected_df.index[2], "Side"] = "SELL"
            expected_df.loc[expected_df.index[3], "Side"] = pd.NA
            expected_df.loc[expected_df.index[4], "Side"] = "DIVIDEND"  # DIV
            expected_df.loc[expected_df.index[0], "Notes"] = "Some note"
            expected_df.loc[expected_df.index[2], "Notes"] = pd.NA
        elif scenario == "action_validation":
            # Already correct, just ensure account is set
            expected_df[Column.Txn.ACCOUNT] = "TEST-ACCOUNT"

        elif scenario == "ignore_columns":
            # Update dates and remove ignored columns
            expected_df.loc[expected_df.index[0], Column.Txn.TXN_DATE] = "2025-02-05"
            expected_df.loc[expected_df.index[1], Column.Txn.TXN_DATE] = "2025-02-07"
            expected_df.loc[expected_df.index[2], Column.Txn.TXN_DATE] = "2025-02-08"
            expected_df = expected_df.drop(columns=["IgnoreMe", "AlsoIgnore"])

        elif scenario == "account_fallback":
            # Add account column with fallback value
            expected_df[Column.Txn.ACCOUNT] = "FALLBACK-ACCOUNT"

        verify_db_contents(expected_df, last_n=expected_count)


def test_import_duplicate_handling(
    temp_ctx: TempContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test duplicate detection for both DB and intra-file duplicates."""
    # The importer logger summarises approved/rejected duplicates only when it is
    # enabled for INFO. Nothing in this file configures that.
    # Setting the level here makes that path deterministic rather than incidental.
    caplog.set_level(logging.INFO, logger="importer")
    with temp_ctx() as ctx:
        config = ctx.config
        ensure_data_exists()
        txn_sheet = config.txn_sheet

        # Test 1: Intra-file duplicates (without approval)
        default_df = _get_default_dataframe(config)
        df_with_dupes = pd.concat([default_df, default_df.iloc[[0]]], ignore_index=True)
        temp_path = config.folio_path.parent / "temp_intra_dupes.xlsx"
        register_test_dataframe(temp_path, df_with_dupes, txn_sheet)

        config.db_path.unlink(missing_ok=True)
        imported_count = import_transactions(temp_path, "TEST-ACCOUNT", txn_sheet)
        expected_count = len(default_df) - 1  # All rows except the duplicated one
        assert imported_count == expected_count

        # Test 2: DB duplicates without approval
        initial_data = {
            Column.Txn.TXN_DATE: ["2024-01-01", "2024-01-02"],
            Column.Txn.ACTION: ["BUY", "SELL"],
            Column.Txn.AMOUNT: [1000.0, 2000.0],
            Column.Txn.CURRENCY: ["USD", "USD"],
            Column.Txn.PRICE: [100.0, 200.0],
            Column.Txn.UNITS: [10.0, 10.0],
            Column.Txn.TICKER: ["AAPL", "MSFT"],
            Column.Txn.ACCOUNT: ["TEST-ACCOUNT"] * 2,
        }

        initial_df = pd.DataFrame(initial_data)
        initial_path = config.folio_path.parent / "initial_transactions.xlsx"
        register_test_dataframe(initial_path, initial_df, txn_sheet)

        config.db_path.unlink(missing_ok=True)
        initial_count = import_transactions(initial_path, "TEST-ACCOUNT", txn_sheet)
        assert initial_count == 2

        # Try to import duplicate - should be rejected
        duplicate_data: dict[str, Any] = {
            Column.Txn.TXN_DATE: ["2024-01-01", "2024-01-03"],
            Column.Txn.ACTION: ["BUY", "DIVIDEND"],
            Column.Txn.AMOUNT: [1000.0, 500.0],
            Column.Txn.CURRENCY: ["USD", "USD"],
            Column.Txn.PRICE: [100.0, 0.0],
            Column.Txn.UNITS: [10.0, 0.0],
            Column.Txn.TICKER: ["AAPL", "AAPL"],
            Column.Txn.ACCOUNT: ["TEST-ACCOUNT"] * 2,
        }

        duplicate_df = pd.DataFrame(duplicate_data)
        duplicate_path = config.folio_path.parent / "duplicate_transactions.xlsx"
        register_test_dataframe(duplicate_path, duplicate_df, txn_sheet)
        no_approval_count = import_transactions(
            duplicate_path,
            "TEST-ACCOUNT",
            txn_sheet,
        )
        assert no_approval_count == 1  # Only the DIVIDEND

        # Test 3: DB duplicate WITH approval
        duplicate_data_with_approval = duplicate_data.copy()
        duplicate_data_with_approval[config.duplicate_approval_column] = ["OK", ""]
        approved_df = pd.DataFrame(duplicate_data_with_approval)
        approved_path = config.folio_path.parent / "approved_duplicates.xlsx"
        register_test_dataframe(approved_path, approved_df, txn_sheet)
        approved_count = import_transactions(approved_path, "TEST-ACCOUNT", txn_sheet)
        assert approved_count == 1  # The approved duplicate

        # Test 4: Intra-file duplicate WITH approval
        approval_column = config.duplicate_approval_column
        intra_approval_data = {
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

        intra_approval_df = pd.DataFrame(intra_approval_data)
        intra_approval_path = config.folio_path.parent / "intra_approval.xlsx"
        register_test_dataframe(intra_approval_path, intra_approval_df, txn_sheet)

        config.db_path.unlink(missing_ok=True)
        intra_approval_count = import_transactions(
            intra_approval_path,
            "TEST-ACCOUNT",
            txn_sheet,
        )
        assert intra_approval_count == 2


def test_import_missing_essential_column(temp_ctx: TempContext) -> None:
    """Test that import fails when essential column is missing."""
    with temp_ctx() as ctx:
        config = ctx.config
        ensure_data_exists()

        default_df = _get_default_dataframe(config)
        essential_to_remove = next(iter(TXN_ESSENTIALS))

        df = default_df.drop(columns=[essential_to_remove])
        txn_sheet = config.txn_sheet
        temp_path = config.folio_path.parent / "temp_missing_essential.xlsx"
        register_test_dataframe(temp_path, df, txn_sheet)
        logging.getLogger("importer").setLevel(logging.CRITICAL)
        with pytest.raises(
            ValueError,
            match=rf"MISSING essential columns: \{{'{essential_to_remove}'\}}\s*",
        ):
            import_transactions(temp_path, None, txn_sheet)


def test_import_statements_missing_columns(
    temp_ctx: TempContext,
) -> None:
    """Test statement import with missing required columns."""
    with temp_ctx() as ctx:
        create_txns_table()
        incomplete_df = pd.DataFrame(
            [
                {
                    "date": "2024-01-16",
                    "amount": 1000.50,
                    # Missing currency, transaction, description
                },
            ],
        )

        statement_file = ctx.config.project_root / "incomplete_statement.xlsx"
        register_test_dataframe(statement_file, incomplete_df)
        result = import_statements(statement_file)
        assert result.settlement_updates == 0
        assert result.transfers_created() == 0


def test_import_statements_empty_file(
    temp_ctx: TempContext,
) -> None:
    """Test statement import with empty file."""
    with temp_ctx() as ctx:
        create_txns_table()
        empty_df = pd.DataFrame()
        statement_file = ctx.config.project_root / "empty_statement.xlsx"
        register_test_dataframe(statement_file, empty_df)
        result = import_statements(statement_file)
        assert result.settlement_updates == 0
        assert result.transfers_created() == 0


def test_import_statements_creates_transfer_from_filename(
    temp_ctx: TempContext,
) -> None:
    """TRFOUT*/TRFIN* rows create TFR_OUT/TFR_IN, with account from the filename."""
    with temp_ctx() as ctx:
        create_txns_table()
        transfer_df = pd.DataFrame(
            [
                {
                    "date": "2025-01-22",
                    "amount": -1000.0,
                    "currency": "CAD",
                    "transaction": "TRFOUTTF",
                    "description": (
                        "Tax-free money transfer out of the account "
                        "(executed at 2025-01-22)"
                    ),
                },
                {
                    "date": "2025-01-22",
                    "amount": 1000.0,
                    "currency": "CAD",
                    "transaction": "TRFINRSP",
                    "description": "RSP account transfer in (executed at 2025-01-22)",
                },
            ],
        )

        statement_file = ctx.config.project_root / "ws_statement_WS-TFSA_202501.xlsx"
        register_test_dataframe(statement_file, transfer_df)
        result = import_statements(statement_file)

        assert result.settlement_updates == 0
        assert result.transfers_created() == 2
        assert result.transfers_rejected == 0

        with get_connection() as conn:
            txns = get_rows(conn, Table.TXNS)

        assert len(txns) == 2

        out_txn = txns[txns[Column.Txn.ACTION] == "TFR_OUT"].iloc[0]
        assert out_txn[Column.Txn.ACCOUNT] == "WS-TFSA"
        assert out_txn[Column.Txn.TXN_DATE] == "2025-01-22"
        assert out_txn[Column.Txn.SETTLE_DATE] == "2025-01-22"
        assert float(out_txn[Column.Txn.AMOUNT]) == -1000.0

        in_txn = txns[txns[Column.Txn.ACTION] == "TFR_IN"].iloc[0]
        assert in_txn[Column.Txn.ACCOUNT] == "WS-TFSA"
        assert in_txn[Column.Txn.TXN_DATE] == "2025-01-22"
        assert in_txn[Column.Txn.SETTLE_DATE] == "2025-01-22"
        assert float(in_txn[Column.Txn.AMOUNT]) == 1000.0


def test_import_statements_skips_cash_transfers(temp_ctx: TempContext) -> None:
    """Cash "Money transfer" rows are skipped; institutional transfers still import."""
    with temp_ctx() as ctx:
        create_txns_table()
        transfer_df = pd.DataFrame(
            [
                {
                    "date": "2025-07-11",
                    "amount": 500.0,
                    "currency": "CAD",
                    "transaction": "TRFIN",
                    "description": "Money transfer received (executed at 2025-07-11)",
                },
                {
                    "date": "2025-07-14",
                    "amount": -200.0,
                    "currency": "CAD",
                    "transaction": "TRFOUT",
                    "description": "Money transfer sent (executed at 2025-07-14)",
                },
                {
                    "date": "2025-07-22",
                    "amount": 1000.0,
                    "currency": "CAD",
                    "transaction": "TRFINRSP",
                    "description": "RSP account transfer in (executed at 2025-07-22)",
                },
            ],
        )

        statement_file = ctx.config.project_root / "ws_statement_WS-RRSP_202507.xlsx"
        register_test_dataframe(statement_file, transfer_df)
        result = import_statements(statement_file)

        assert result.transfers_created() == 1
        assert result.transfers_skipped == 2
        assert result.transfers_rejected == 0

        with get_connection() as conn:
            txns = get_rows(conn, Table.TXNS)

        assert len(txns) == 1
        assert txns.iloc[0][Column.Txn.ACTION] == "TFR_IN"
        assert float(txns.iloc[0][Column.Txn.AMOUNT]) == 1000.0


def test_import_statements_rejects_transfer_without_account_in_filename(
    temp_ctx: TempContext,
) -> None:
    """A transfer row is rejected, not silently dropped, without a parseable account."""
    with temp_ctx() as ctx:
        create_txns_table()
        transfer_df = pd.DataFrame(
            [
                {
                    "date": "2025-07-22",
                    "amount": -4420.0,
                    "currency": "CAD",
                    "transaction": "TRFOUTTF",
                    "description": "Tax-free money transfer out of the account",
                },
            ],
        )

        statement_file = ctx.config.project_root / "unnamed_statement.xlsx"
        register_test_dataframe(statement_file, transfer_df)
        result = import_statements(statement_file)

        assert result.transfers_created() == 0
        assert result.transfers_rejected == 1

        with get_connection() as conn:
            txns = get_rows(conn, Table.TXNS)
        assert txns.empty


# Helper functions
def _get_default_dataframe(config: Config) -> pd.DataFrame:
    """Get the default DataFrame from the transactions parquet."""
    return pd.read_parquet(config.txn_parquet, engine="fastparquet")
