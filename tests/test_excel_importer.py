"""Tests for the excel_importer module."""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING, Any

import pandas as pd
import pytest

from db import create_txns_table, get_connection, get_rows, helpers, txn_count
from domain import TXN_ESSENTIALS, Column, SettlementOutcome, Table
from importers import import_statements, import_transactions, insert_transactions
from ingest import prepare_transactions
from models import ImportResults

from .fixtures.dataframe_cache import register_test_dataframe
from .helpers.dataframe import verify_db_contents
from .helpers.seed import (
    STATEMENT_PRICE,
    STATEMENT_SETTLE_DATE,
    STATEMENT_TRADE_DATE,
    TSX_TICKER,
    VENTURE_TICKER,
    seed_transaction,
    statement_buy,
)

if TYPE_CHECKING:
    from pathlib import Path

    from .helpers.context import TempContext

logger: logging.Logger = logging.getLogger(__name__)


@pytest.mark.parametrize(
    (
        "test_data",
        "expected_count",
        "expected_rows",
        "config_overrides",
        "account",
        "formatted",
    ),
    [
        # Mega formatting test covering all validation paths
        pytest.param(
            {
                Column.Txn.TXN_DATE: [
                    "2023-01-01",  # 0: Good case: all columns perfect
                    "01/02/2023",  # 1: Auto-formatted date (MM/DD/YYYY -> YYYY-MM-DD)
                    "2023-01-03T10:30:45Z",  # 2: ISO 8601 format with timezone
                    "INVALID_DATE",  # 3: Invalid date: should be rejected
                    "",  # 4: Empty date: should be rejected
                    "2023-01-05 15:45:30",  # 5: Datetime format with space
                    pd.NA,  # 6: Invalid action: should be rejected
                    "2023-01-07",  # 7: Action abbreviation (will be normalized)
                    "2023-01-08",  # 8: Empty amount: should be rejected
                    "2023-01-09",  # 9: Invalid amount format: should be rejected
                    "2023-01-10",  # 10: Invalid currency: should be rejected
                    "2023-01-11",  # 11: Missing currency: should be rejected
                    "2023-01-12T20:15:30.123456Z",  # 12: ISO format with ms
                    "2023-01-13",  # 13: Empty price: should be rejected
                    "2023-01-14",  # 14: Invalid price format: should be rejected
                    "2023-01-15",  # 15: Empty units: should be rejected
                    "2023-01-16",  # 16: Invalid units format: should be rejected
                    "2023-01-17",  # 17: Empty ticker (valid: becomes NULL)
                    "2023-01-18",  # 18: NULL ticker (valid: stays NULL)
                    "2023-01-19",  # 19: Invalid ticker format: should be rejected
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
            "TEST-ACCOUNT",
            {
                0: {Column.Txn.TICKER: "AAPL"},
                1: {Column.Txn.TXN_DATE: "2023-01-02"},
                2: {Column.Txn.TXN_DATE: "2023-01-03", Column.Txn.TICKER: "AAPL"},
                3: {
                    Column.Txn.TXN_DATE: "2023-01-07",
                    Column.Txn.ACTION: "DIVIDEND",
                    Column.Txn.AMOUNT: 1000.0,
                },
                4: {Column.Txn.TXN_DATE: "2023-01-12", Column.Txn.CURRENCY: "USD"},
                5: {Column.Txn.TXN_DATE: "2023-01-17", Column.Txn.TICKER: pd.NA},
                6: {Column.Txn.TXN_DATE: "2023-01-18", Column.Txn.TICKER: pd.NA},
            },
            id="formatting_validation",
        ),
        # Optional fields test: covers all 5 field types
        pytest.param(
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
            "TEST-ACCOUNT",
            {
                0: {
                    "Fees": "5.95",  # $5.95 -> 5.95
                    "Custom Date": "2023-01-03",
                    "Trade Currency": "USD",  # US$
                    "Side": "BUY",  # B -> BUY
                    "Notes": "Some note",
                },
                2: {
                    "Fees": pd.NA,  # "" -> NULL
                    "Custom Date": "2023-02-05",
                    "Side": "SELL",
                    "Notes": pd.NA,
                },
                3: {"Custom Date": pd.NA, "Trade Currency": pd.NA, "Side": pd.NA},
                4: {"Custom Date": "2023-02-07", "Side": "DIVIDEND"},  # DIV
            },
            id="optional_fields",
        ),
        # Action validation test
        pytest.param(
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
            "TEST-ACCOUNT",
            {},
            id="action_validation",
        ),
        # Ignore columns test
        pytest.param(
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
            "TEST-ACCOUNT",
            {
                0: {Column.Txn.TXN_DATE: "2025-02-05"},
                1: {Column.Txn.TXN_DATE: "2025-02-07"},
                2: {Column.Txn.TXN_DATE: "2025-02-08"},
            },
            id="ignore_columns",
        ),
        # Account fallback test
        pytest.param(
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
            "FALLBACK-ACCOUNT",
            {},
            id="account_fallback",
        ),
    ],
)
def test_import_scenarios(
    temp_ctx: TempContext,
    test_data: dict[str, Any],
    expected_count: int,
    expected_rows: list[int],
    config_overrides: dict[str, Any],
    account: str,
    formatted: dict[int, dict[str, Any]],
) -> None:
    """Formatting, validation and optional fields, each checked against the db.

    Args:
        temp_ctx: The temp folio factory.
        test_data: The rows the source file holds.
        expected_count: How many rows import.
        expected_rows: Which source rows those are.
        config_overrides: Settings the scenario needs.
        account: The fallback account the import is given, and every row gets.
        formatted: Per imported row (by position), each value formatting changes.
    """
    with temp_ctx(**config_overrides) as ctx:
        temp_path = ctx.config.folio_path.parent / "test_import.xlsx"
        df = pd.DataFrame(test_data)
        register_test_dataframe(temp_path, df)

        imported_count = import_transactions(temp_path, account).imported_count()

        assert imported_count == expected_count, (
            f"Expected {expected_count} imports but got {imported_count}"
        )
        expected_df = df.iloc[expected_rows].copy()
        expected_df[Column.Txn.ACCOUNT] = account
        for position, values in formatted.items():
            for column, value in values.items():
                expected_df.loc[expected_df.index[position], column] = value
        # An essential column is imported even when header_ignore lists it.
        ignored = [
            column
            for column in config_overrides.get("header_ignore", [])
            if column not in TXN_ESSENTIALS
        ]
        expected_df = expected_df.drop(columns=ignored)
        verify_db_contents(expected_df, last_n=expected_count)


def _register(path: Path, data: dict[str, Any], sheet: str) -> Path:
    """Register `data` as the workbook at `path`, and return the path."""
    register_test_dataframe(path, pd.DataFrame(data), sheet)
    return path


def _imported(path: Path, sheet: str) -> int:
    """Import `path` into TEST-ACCOUNT and return how many rows went in."""
    return import_transactions(path, "TEST-ACCOUNT", sheet).imported_count()


# Two rows already in the folio, and a file repeating the first beside a new one.
STORED_ROWS: dict[str, Any] = {
    Column.Txn.TXN_DATE: ["2024-01-01", "2024-01-02"],
    Column.Txn.ACTION: ["BUY", "SELL"],
    Column.Txn.AMOUNT: [1000.0, 2000.0],
    Column.Txn.CURRENCY: ["USD", "USD"],
    Column.Txn.PRICE: [100.0, 200.0],
    Column.Txn.UNITS: [10.0, 10.0],
    Column.Txn.TICKER: ["AAPL", "MSFT"],
    Column.Txn.ACCOUNT: ["TEST-ACCOUNT"] * 2,
}
REPEATING_ROWS: dict[str, Any] = {
    Column.Txn.TXN_DATE: ["2024-01-01", "2024-01-03"],
    Column.Txn.ACTION: ["BUY", "DIVIDEND"],
    Column.Txn.AMOUNT: [1000.0, 500.0],
    Column.Txn.CURRENCY: ["USD", "USD"],
    Column.Txn.PRICE: [100.0, 0.0],
    Column.Txn.UNITS: [10.0, 0.0],
    Column.Txn.TICKER: ["AAPL", "AAPL"],
    Column.Txn.ACCOUNT: ["TEST-ACCOUNT"] * 2,
}


@pytest.fixture
def importer_info(caplog: pytest.LogCaptureFixture) -> None:
    """Enable the importer logger for INFO, where duplicates are summarised.

    Nothing in this file configures that level otherwise, so setting it makes
    the summary path deterministic rather than incidental.
    """
    caplog.set_level(logging.INFO, logger="importer")


@pytest.mark.usefixtures("importer_info")
def test_a_row_repeated_within_a_file_is_held_back(temp_ctx: TempContext) -> None:
    """Without approval, neither copy of a repeated row is imported."""
    with temp_ctx() as ctx:
        sheet = ctx.config.txn_sheet
        rows = _valid_rows()
        path = ctx.config.folio_path.parent / "intra_dupes.xlsx"
        register_test_dataframe(
            path,
            pd.concat([rows, rows.iloc[[0]]], ignore_index=True),
            sheet,
        )

        assert _imported(path, sheet) == len(rows) - 1


@pytest.mark.usefixtures("importer_info")
def test_a_row_already_in_the_folio_needs_approval(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        sheet = ctx.config.txn_sheet
        folder = ctx.config.folio_path.parent
        assert (
            _imported(_register(folder / "stored.xlsx", STORED_ROWS, sheet), sheet) == 2
        )

        repeating = _register(folder / "repeating.xlsx", REPEATING_ROWS, sheet)
        assert _imported(repeating, sheet) == 1  # Only the DIVIDEND

        approved = {
            **REPEATING_ROWS,
            ctx.config.duplicate_approval_column: ["OK", ""],
        }
        approved_path = _register(folder / "approved.xlsx", approved, sheet)
        assert _imported(approved_path, sheet) == 1  # The approved duplicate


@pytest.mark.usefixtures("importer_info")
def test_an_approved_repeat_within_a_file_is_imported(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        sheet = ctx.config.txn_sheet
        rows = {
            Column.Txn.TXN_DATE: ["2024-02-01", "2024-02-01", "2024-02-02"],
            Column.Txn.ACTION: ["BUY", "BUY", "SELL"],
            Column.Txn.AMOUNT: [1000.0, 1000.0, 2000.0],
            Column.Txn.CURRENCY: ["USD", "USD", "USD"],
            Column.Txn.PRICE: [100.0, 100.0, 200.0],
            Column.Txn.UNITS: [10.0, 10.0, 10.0],
            Column.Txn.TICKER: ["AAPL", "AAPL", "MSFT"],
            Column.Txn.ACCOUNT: ["TEST-ACCOUNT"] * 3,
            ctx.config.duplicate_approval_column: ["", "OK", ""],
        }
        path = _register(
            ctx.config.folio_path.parent / "intra_approval.xlsx",
            rows,
            sheet,
        )

        assert _imported(path, sheet) == 2


def test_a_batch_the_table_refuses_is_not_stored_and_names_its_row(
    temp_ctx: TempContext,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row ingest passed but the table's checks refuse takes none of its batch."""
    caplog.set_level(logging.INFO, logger="importer")
    monkeypatch.setattr(logging.getLogger("importer"), "propagate", True)
    with temp_ctx():
        create_txns_table()
        rows = pd.DataFrame(
            {
                Column.Txn.TXN_DATE: ["2024-01-02", "2024-01-03", "2024-01-04"],
                Column.Txn.ACTION: ["BUY"] * 3,
                Column.Txn.AMOUNT: [-100.0] * 3,
                Column.Txn.CURRENCY: ["USD"] * 3,
                Column.Txn.PRICE: [10.0] * 3,
                Column.Txn.UNITS: [10.0] * 3,
                # The table keeps tickers in upper case; the third is not.
                Column.Txn.TICKER: ["MSFT", "MSFT", "msft"],
                Column.Txn.ACCOUNT: ["TEST-ACCOUNT"] * 3,
            },
        )
        passed = ImportResults(final_df=rows)
        monkeypatch.setattr(
            "importers.excel_importer.prepare_transactions",
            lambda *_args, **_kwargs: passed,
        )

        with pytest.raises(sqlite3.IntegrityError):
            insert_transactions(rows)

        assert txn_count() == 0
        assert "Row 2/3: SUCCESS" in caplog.text
        assert "Row 3/3: FAILED" in caplog.text


def test_the_table_refuses_a_currency_the_folio_does_not_support(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EUR is priced for quotes, but no write path may put it on a transaction."""
    with temp_ctx():
        create_txns_table()
        rows = _valid_rows().assign(**{Column.Txn.CURRENCY: ["USD", "EUR", "USD"]})
        monkeypatch.setattr(
            "importers.excel_importer.prepare_transactions",
            lambda *_args, **_kwargs: ImportResults(final_df=rows),
        )

        with pytest.raises(sqlite3.IntegrityError):
            insert_transactions(rows)

        assert txn_count() == 0


def test_only_the_final_frame_gains_the_columns_the_table_has(
    temp_ctx: TempContext,
) -> None:
    """A table column the file lacks is added to `final_df`, not to `read_df`."""
    with temp_ctx():
        create_txns_table()
        helpers.sync_txns_table_columns(_valid_rows().assign(Memo="note"))

        results = prepare_transactions(_valid_rows(), map_headers=False)

    assert "Memo" in results.final_df.columns
    assert "Memo" not in results.read_df.columns


def test_a_fallback_account_is_not_written_into_the_source_frame(
    temp_ctx: TempContext,
) -> None:
    """The account the file lacks is filled in for the import, not for `read_df`."""
    with temp_ctx():
        source = _valid_rows().drop(columns=[Column.Txn.ACCOUNT])

        results = prepare_transactions(source, account="TEST-ACCOUNT")

    assert Column.Txn.ACCOUNT not in results.read_df.columns
    assert Column.Txn.ACCOUNT not in source.columns
    assert results.final_df[Column.Txn.ACCOUNT].eq("TEST-ACCOUNT").all()


def test_import_missing_essential_column(
    temp_ctx: TempContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that import fails when essential column is missing."""
    with temp_ctx() as ctx:
        config = ctx.config

        default_df = _valid_rows()
        essential_to_remove = next(iter(TXN_ESSENTIALS))

        df = default_df.drop(columns=[essential_to_remove])
        txn_sheet = config.txn_sheet
        temp_path = config.folio_path.parent / "temp_missing_essential.xlsx"
        register_test_dataframe(temp_path, df, txn_sheet)
        caplog.set_level(logging.CRITICAL, logger="importer")
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
                    "date": "2025-03-11",
                    "amount": -2750.0,
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


def test_import_statements_matches_transform_corrected_ticker(
    temp_ctx: TempContext,
) -> None:
    """A statement ticker goes through the same transform rules the import did.

    The importer appends `.TO` to every CAD ticker, so a security listed on the
    TSX *Venture* needs a transform rule to correct the venue. Without applying
    that rule here the statement looks for `VNTKR.TO` and never finds `VNTKR.V`.
    """
    venue_rule = {
        "transforms": {
            "rules": [
                {
                    "conditions": {"Ticker": [f"{VENTURE_TICKER}.TO"]},
                    "actions": {"Ticker": f"{VENTURE_TICKER}.V"},
                },
            ],
        },
    }
    with temp_ctx(venue_rule) as ctx:
        create_txns_table()
        txn_id = seed_transaction(
            action="BUY",
            date=STATEMENT_TRADE_DATE,
            account="WS-PERSONAL",
            currency="CAD",
            ticker=f"{VENTURE_TICKER}.V",
            amount="-116.80",
            price="58.40",
            units="2",
        )

        statement_df = pd.DataFrame(
            [
                statement_buy(
                    ticker=VENTURE_TICKER,
                    name="Venture Test Corp",
                    units=2,
                    price="58.40",
                ),
            ],
        )
        statement_file = (
            ctx.config.project_root / "ws_statement_WS-PERSONAL_202605.xlsx"
        )
        register_test_dataframe(statement_file, statement_df)
        result = import_statements(statement_file)

        assert result.settlement_updates == 1
        assert [m.ticker for m in result.settlement_matches] == [f"{VENTURE_TICKER}.V"]

        with get_connection() as conn:
            txns = get_rows(conn, Table.TXNS)
        row = txns[txns[Column.Txn.TXN_ID] == txn_id].iloc[0]
        assert row[Column.Txn.SETTLE_DATE] == STATEMENT_SETTLE_DATE
        assert int(row[Column.Txn.SETTLE_CALCULATED]) == 0


def test_import_statements_matches_identical_trades_by_account(
    temp_ctx: TempContext,
) -> None:
    """The same trade in two accounts settles per account, not ambiguously.

    Buying one share at the same price on the same day in two accounts is
    ordinary. Only the account, which the statement filename carries, tells the
    two rows apart.
    """
    with temp_ctx() as ctx:
        create_txns_table()
        shared = {
            "action": "BUY",
            "date": STATEMENT_TRADE_DATE,
            "currency": "CAD",
            "ticker": f"{TSX_TICKER}.TO",
            "amount": f"-{STATEMENT_PRICE}",
            "price": STATEMENT_PRICE,
            "units": "1",
        }
        personal_id = seed_transaction(account="WS-PERSONAL", **shared)
        tfsa_id = seed_transaction(account="WS-TFSA", **shared)

        statement_df = pd.DataFrame(
            [
                statement_buy(),
            ],
        )
        statement_file = ctx.config.project_root / "ws_statement_WS-TFSA_202604.xlsx"
        register_test_dataframe(statement_file, statement_df)
        result = import_statements(statement_file)

        assert result.settlement_updates == 1
        assert [m.txn_id for m in result.settlement_matches] == [tfsa_id]

        with get_connection() as conn:
            txns = get_rows(conn, Table.TXNS)

        tfsa_row = txns[txns[Column.Txn.TXN_ID] == tfsa_id].iloc[0]
        assert tfsa_row[Column.Txn.SETTLE_DATE] == STATEMENT_SETTLE_DATE
        assert int(tfsa_row[Column.Txn.SETTLE_CALCULATED]) == 0

        # The personal row keeps the date the market calendar gave it.
        personal_row = txns[txns[Column.Txn.TXN_ID] == personal_id].iloc[0]
        assert int(personal_row[Column.Txn.SETTLE_CALCULATED]) == 1


def test_import_statements_records_unmatched_rows(temp_ctx: TempContext) -> None:
    """A statement row matching nothing is reported, not silently dropped.

    Rows that never settle on a business day (a dividend here) are not
    candidates at all, so they are skipped rather than reported unmatched.
    """
    with temp_ctx() as ctx:
        create_txns_table()
        seed_transaction(
            action="BUY",
            date=STATEMENT_TRADE_DATE,
            account="WS-TFSA",
            currency="CAD",
            ticker=f"{TSX_TICKER}.TO",
            amount=f"-{STATEMENT_PRICE}",
            price=STATEMENT_PRICE,
            units="1",
        )

        statement_df = pd.DataFrame(
            [
                statement_buy(price="99.99"),
                {
                    "date": "2026-04-30",
                    "amount": 7.30,
                    "currency": "CAD",
                    "transaction": "DIV",
                    "description": (
                        f"{TSX_TICKER} - Test Corp: Cash dividend distribution, "
                        "received on 2026-04-30, record date of 2026-04-15"
                    ),
                },
            ],
        )
        statement_file = ctx.config.project_root / "ws_statement_WS-TFSA_202604.xlsx"
        register_test_dataframe(statement_file, statement_df)
        result = import_statements(statement_file)

        assert result.settlement_updates == 0
        assert result.settlement_candidates() == 1
        unplaced = result.settlement_unplaced()
        assert len(unplaced) == 1
        assert unplaced[0].outcome is SettlementOutcome.UNMATCHED
        assert unplaced[0].candidates == 0
        assert unplaced[0].account == "WS-TFSA"


def test_import_statements_reports_already_settled_rows(
    temp_ctx: TempContext,
) -> None:
    """Re-importing a statement reports its rows settled, not unmatched.

    Only transactions with a calculated date are candidates, so a second run
    matches nothing. That is routine, and must not read as a failure.
    """
    with temp_ctx() as ctx:
        create_txns_table()
        txn_id = seed_transaction(
            action="BUY",
            date=STATEMENT_TRADE_DATE,
            account="WS-TFSA",
            currency="CAD",
            ticker=f"{TSX_TICKER}.TO",
            amount=f"-{STATEMENT_PRICE}",
            price=STATEMENT_PRICE,
            units="1",
        )

        statement_df = pd.DataFrame(
            [
                statement_buy(),
            ],
        )
        statement_file = ctx.config.project_root / "ws_statement_WS-TFSA_202604.xlsx"
        register_test_dataframe(statement_file, statement_df)

        first = import_statements(statement_file)
        assert first.settlement_updates == 1

        second = import_statements(statement_file)

        assert second.settlement_updates == 0
        assert second.settlement_candidates() == 1
        assert second.settlement_already_settled() == 1
        # Nothing is wrong, so nothing is reported as unplaced.
        assert second.settlement_unplaced() == []

        match = second.settlement_matches[0]
        assert match.outcome is SettlementOutcome.ALREADY_SETTLED
        assert match.txn_id == txn_id

        # The date from the first run is untouched.
        with get_connection() as conn:
            txns = get_rows(conn, Table.TXNS)
        row = txns[txns[Column.Txn.TXN_ID] == txn_id].iloc[0]
        assert row[Column.Txn.SETTLE_DATE] == STATEMENT_SETTLE_DATE


def test_import_statements_reports_ambiguity_without_an_account(
    temp_ctx: TempContext,
) -> None:
    """Without an account in the filename, identical trades stay ambiguous."""
    with temp_ctx() as ctx:
        create_txns_table()
        shared = {
            "action": "BUY",
            "date": STATEMENT_TRADE_DATE,
            "currency": "CAD",
            "ticker": f"{TSX_TICKER}.TO",
            "amount": f"-{STATEMENT_PRICE}",
            "price": STATEMENT_PRICE,
            "units": "1",
        }
        seed_transaction(account="WS-PERSONAL", **shared)
        seed_transaction(account="WS-TFSA", **shared)

        statement_df = pd.DataFrame(
            [
                statement_buy(),
            ],
        )
        statement_file = ctx.config.project_root / "unnamed_statement.xlsx"
        register_test_dataframe(statement_file, statement_df)
        result = import_statements(statement_file)

        assert result.settlement_updates == 0
        unplaced = result.settlement_unplaced()
        assert len(unplaced) == 1
        assert unplaced[0].outcome is SettlementOutcome.AMBIGUOUS
        assert unplaced[0].candidates == 2
        assert unplaced[0].account is None


# Helper functions
def _valid_rows() -> pd.DataFrame:
    """Build a few distinct rows the importer accepts as they are."""
    return pd.DataFrame(
        {
            Column.Txn.TXN_DATE: ["2025-08-14", "2025-08-15", "2025-08-18"],
            Column.Txn.ACTION: ["BUY", "BUY", "SELL"],
            Column.Txn.AMOUNT: [-1000.0, -400.0, 600.0],
            Column.Txn.CURRENCY: ["USD", "USD", "USD"],
            Column.Txn.PRICE: [100.0, 40.0, 120.0],
            Column.Txn.UNITS: [10.0, 10.0, -5.0],
            Column.Txn.TICKER: ["TESTTKR", "OTHER", "TESTTKR"],
            Column.Txn.ACCOUNT: ["TEST-ACCOUNT"] * 3,
        },
    )
