"""Tests to verify the functionality of Excel exports."""

from contextlib import _GeneratorContextManager
from typing import Callable

import pandas as pd

from app.app_context import AppContext
from db import db
from exporters.excel_exporter import TransactionExporter
from mock.folio_setup import ensure_folio_exists
from utils.constants import Table

from .utils.dataframe_utils import verify_db_contents


def test_export_transactions_full(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    with temp_config() as ctx:
        config = ctx.config
        ensure_folio_exists()
        with db.get_connection() as conn:
            db_count = db.get_row_count(conn, Table.TXNS.value)
        transaction_exporter = TransactionExporter()
        export_count: int = transaction_exporter.export_full()
        excel_df = pd.read_excel(config.folio_path, config.transactions_sheet())
        assert export_count == db_count
        verify_db_contents(excel_df, len(excel_df))
