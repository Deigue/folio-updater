"""Tests to verify the functionality of Parquet transaction exports."""

import pandas as pd

from exporters.parquet_exporter import ParquetExporter
from mock.folio_setup import ensure_data_exists

from .test_types import TempContext
from .utils.dataframe_utils import verify_db_contents

GENERATED_TRANSACTIONS = 50


def test_export_transactions_parquet(temp_ctx: TempContext) -> None:
    """Test exporting transactions to Parquet with updates and duplicates."""
    with temp_ctx() as ctx:
        config = ctx.config
        ensure_data_exists()

        # Test 1: Initial export from database to Parquet
        exporter = ParquetExporter()
        export_count: int = exporter.export_transactions()
        assert export_count == GENERATED_TRANSACTIONS

        # Read from Parquet and verify
        parquet_path = config.txn_parquet
        assert parquet_path.exists()
        parquet_df = pd.read_parquet(parquet_path, engine="pyarrow")
        verify_db_contents(parquet_df, len(parquet_df))
