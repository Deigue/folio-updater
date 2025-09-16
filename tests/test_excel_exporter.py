"""Tests to verify the functionality of Excel exports."""

from contextlib import _GeneratorContextManager
from typing import Callable

import pandas as pd
import pandas.testing as pd_testing

from app.app_context import AppContext
from db import db
from exporters.excel_exporter import TransactionExporter
from mock.folio_setup import ensure_folio_exists
from utils.constants import Column, Table


def test_export_transactions_full(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    with temp_config() as ctx:
        config = ctx.config
        ensure_folio_exists()
        with db.get_connection() as conn:
            db_count = db.get_row_count(conn, Table.TXNS.value)
            table_df = db.get_rows(conn, Table.TXNS.value)
        exporter = TransactionExporter()
        export_count: int = exporter.export_full()
        exported_df = pd.read_excel(config.folio_path, config.transactions_sheet())
        assert export_count == db_count

        # Remove the TxnID column for comparison if it exists
        table_df = table_df.drop(columns=[Column.Txn.TXN_ID.value], errors="ignore")

        # TODO@deigue: Centralize _verify_db_contents and use across pytests.
        pd_testing.assert_frame_equal(
            exported_df,
            table_df,
        )
