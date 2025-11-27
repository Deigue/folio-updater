"""Tests to verify the functionality of Parquet transaction exports."""

import pandas as pd

from exporters import ParquetExporter
from mock.folio_setup import ensure_data_exists
from mock.mock_data import DEFAULT_TXN_COUNT
from utils.constants import DEFAULT_TICKERS

from .test_types import TempContext
from .utils.dataframe_utils import verify_db_contents


def test_export_transactions_parquet(temp_ctx: TempContext) -> None:
    """Test exporting transactions to Parquet with updates and duplicates."""
    with temp_ctx() as ctx:
        config = ctx.config
        ensure_data_exists()

        # Test 1: Initial export from database to Parquet
        exporter = ParquetExporter()
        export_count: int = exporter.export_transactions()
        assert export_count == DEFAULT_TXN_COUNT * len(DEFAULT_TICKERS)

        # Read from Parquet and verify
        parquet_path = config.txn_parquet
        assert parquet_path.exists()
        parquet_df = pd.read_parquet(parquet_path, engine="fastparquet")
        verify_db_contents(parquet_df, len(parquet_df))
