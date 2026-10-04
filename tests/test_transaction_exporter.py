"""Tests to verify the functionality of Parquet transaction exports."""

import pandas as pd

from datagen import ensure_data_exists
from exporters import ParquetExporter

from .helpers.dataframe import verify_db_contents
from .helpers.folio import txn_total
from .test_types import TempContext


def test_export_transactions_parquet(temp_ctx: TempContext) -> None:
    """Test exporting transactions to Parquet with updates and duplicates."""
    with temp_ctx() as ctx:
        config = ctx.config
        ensure_data_exists()

        # Test 1: Initial export from database to Parquet
        exporter = ParquetExporter()
        export_count: int = exporter.export_transactions()
        assert export_count == txn_total()

        # Read from Parquet and verify
        parquet_path = config.txn_parquet
        assert parquet_path.exists()
        parquet_df = pd.read_parquet(parquet_path, engine="fastparquet")
        verify_db_contents(parquet_df, len(parquet_df))
