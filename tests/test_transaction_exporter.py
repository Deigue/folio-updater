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


def test_export_full_and_update_empty_db(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    with temp_config() as ctx:
        config = ctx.config
        ensure_folio_exists()
        # Test 1: Export from database to Excel
        transaction_exporter = TransactionExporter()
        export_count: int = transaction_exporter.export_full()
        assert export_count == GENERATED_TRANSACTIONS
        excel_df = pd.read_excel(config.folio_path, config.transactions_sheet())
        verify_db_contents(excel_df, len(excel_df))
        # Test 2: Update from empty database (no changes)
        config.db_path.unlink()
        update_count = transaction_exporter.export_update()
        assert update_count == 0
        excel_df_after = pd.read_excel(config.folio_path, config.transactions_sheet())
        pd_testing.assert_frame_equal(excel_df_after, excel_df)


def test_export_update_scenarios(
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
