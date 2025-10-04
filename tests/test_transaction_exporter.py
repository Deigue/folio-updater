"""Tests to verify the functionality of Excel exports."""

from contextlib import _GeneratorContextManager
from typing import Callable

import pandas as pd
import pandas.testing as pd_testing

from app.app_context import AppContext
from db import db
from exporters.transaction_exporter import TransactionExporter
from mock.folio_setup import ensure_folio_exists
from utils.constants import Column, Table

from .utils.dataframe_utils import verify_db_contents

GENERATED_TRANSACTIONS = 50


def test_export_full(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    with temp_config() as ctx:
        config = ctx.config
        ensure_folio_exists()
        with db.get_connection() as conn:
            db_count = db.get_row_count(conn, Table.TXNS)
        transaction_exporter = TransactionExporter()
        export_count: int = transaction_exporter.export_full()
        excel_df = pd.read_excel(config.folio_path, config.transactions_sheet())
        assert export_count == db_count
        verify_db_contents(excel_df, len(excel_df))


def test_export_update_empty_excel_sheet(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test update export when Excel sheet is empty."""
    with temp_config() as ctx:
        config = ctx.config
        ensure_folio_exists()

        # Create empty transactions sheet
        with pd.ExcelWriter(
            config.folio_path,
            engine="openpyxl",
            mode="a",
            if_sheet_exists="replace",
        ) as writer:
            empty_df = pd.DataFrame()
            empty_df.to_excel(
                writer,
                sheet_name=config.transactions_sheet(),
                index=False,
            )

        transaction_exporter = TransactionExporter()

        # Since Excel is empty, it should export all transactions from DB
        update_count: int = transaction_exporter.export_update()
        assert update_count == GENERATED_TRANSACTIONS


def test_export_update_empty_database(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test update export when database is empty."""
    with temp_config() as ctx:
        config = ctx.config
        ensure_folio_exists()
        config.db_path.unlink()  # Start with empty DB
        excel_df = pd.read_excel(config.folio_path, config.transactions_sheet())
        original_count = len(excel_df)
        transaction_exporter = TransactionExporter()

        # Should return 0 when database is empty
        update_count: int = transaction_exporter.export_update()
        assert update_count == 0

        # Verify Excel contents unchanged
        excel_df = pd.read_excel(config.folio_path, config.transactions_sheet())
        assert len(excel_df) == original_count


def test_export_update_with_newer_transactions(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test update export with transactions having newer dates."""
    with temp_config() as ctx:
        config = ctx.config
        ensure_folio_exists()

        # Seed new dates based on existing latest date in Excel.
        excel_df_initial = pd.read_excel(config.folio_path, config.transactions_sheet())
        latest_date = excel_df_initial[Column.Txn.TXN_DATE].max()
        newer_dates = [
            pd.to_datetime(latest_date) + pd.Timedelta(days=1),
            pd.to_datetime(latest_date) + pd.Timedelta(days=2),
        ]
        newer_dates_str = [d.strftime("%Y-%m-%d") for d in newer_dates]

        # Add new transactions with dates newer than the latest existing date
        # Using dates well after the latest to ensure they are clearly newer
        newer_transactions = pd.DataFrame(
            {
                Column.Txn.TXN_DATE: newer_dates_str,
                Column.Txn.ACTION: ["BUY", "SELL"],
                Column.Txn.AMOUNT: [1000.0, 500.0],
                Column.Txn.CURRENCY: ["CAD", "USD"],
                Column.Txn.PRICE: [100.0, 50.0],
                Column.Txn.UNITS: [10.0, 10.0],
                Column.Txn.TICKER: ["TEST", "DEMO"],
                Column.Txn.ACCOUNT: ["TEST-ACCOUNT", "TEST-ACCOUNT"],
            },
        )
        new_txn_count = len(newer_transactions)
        with db.get_connection() as conn:
            newer_transactions.to_sql(
                Table.TXNS,
                conn,
                if_exists="append",
                index=False,
            )

        transaction_exporter = TransactionExporter()
        update_count: int = transaction_exporter.export_update()
        assert update_count == new_txn_count
        excel_df_after = pd.read_excel(config.folio_path, config.transactions_sheet())
        verify_db_contents(excel_df_after, len(excel_df_after))


def test_export_update_with_duplicate_transactions(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test update export with duplicate transactions on the same date."""
    with temp_config() as ctx:
        config = ctx.config
        ensure_folio_exists()
        excel_df_initial = pd.read_excel(config.folio_path, config.transactions_sheet())
        latest_date = excel_df_initial[Column.Txn.TXN_DATE].max()
        latest_transactions = excel_df_initial[
            excel_df_initial[Column.Txn.TXN_DATE] == latest_date
        ].copy()

        duplicate_transactions = latest_transactions.head(3).copy()
        with db.get_connection() as conn:
            duplicate_transactions.to_sql(
                Table.TXNS,
                conn,
                if_exists="append",
                index=False,
            )

        transaction_exporter = TransactionExporter()
        update_count: int = transaction_exporter.export_update()
        assert update_count == 0
        excel_df_after = pd.read_excel(config.folio_path, config.transactions_sheet())
        pd_testing.assert_frame_equal(excel_df_after, excel_df_initial)


def test_export_update_mixed_duplicate_and_new_transactions(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test update export with both duplicate and new transactions."""
    with temp_config() as ctx:
        config = ctx.config
        ensure_folio_exists()
        excel_df_initial = pd.read_excel(config.folio_path, config.transactions_sheet())
        initial_excel_count = len(excel_df_initial)
        latest_date = excel_df_initial[Column.Txn.TXN_DATE].max()
        latest_transactions = excel_df_initial[
            excel_df_initial[Column.Txn.TXN_DATE] == latest_date
        ].copy()

        duplicate_transactions = latest_transactions.head(3).copy()
        # Create new transactions on the same latest date (different data)
        new_transactions_same_date = pd.DataFrame(
            {
                Column.Txn.TXN_DATE: [latest_date, latest_date],
                Column.Txn.ACTION: ["BUY", "SELL"],
                Column.Txn.AMOUNT: [2000.0, 1500.0],
                Column.Txn.CURRENCY: ["CAD", "USD"],
                Column.Txn.PRICE: [200.0, 150.0],
                Column.Txn.UNITS: [10.0, 10.0],
                Column.Txn.TICKER: ["UNIQUE1", "UNIQUE2"],
                Column.Txn.ACCOUNT: ["TEST-ACCOUNT", "TEST-ACCOUNT"],
            },
        )
        new_txn_count = len(new_transactions_same_date)

        # Insert both duplicate and new transactions into database
        with db.get_connection() as conn:
            duplicate_transactions.to_sql(
                Table.TXNS,
                conn,
                if_exists="append",
                index=False,
            )
            new_transactions_same_date.to_sql(
                Table.TXNS,
                conn,
                if_exists="append",
                index=False,
            )

        # Perform update export - should only export new transactions, not duplicates
        transaction_exporter = TransactionExporter()
        update_count: int = transaction_exporter.export_update()
        assert update_count == new_txn_count

        # Verify Excel contains only the new transactions
        excel_df_after = pd.read_excel(config.folio_path, config.transactions_sheet())
        assert len(excel_df_after) == initial_excel_count + new_txn_count
        pd_testing.assert_frame_equal(
            excel_df_after,
            pd.concat(
                [excel_df_initial, new_transactions_same_date],
                ignore_index=True,
            ),
        )
