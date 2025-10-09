"""Tests for Forex exports with Parquet storage."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable
from unittest.mock import patch

import pandas as pd
import pytest

from db import db, schema_manager
from exporters.parquet_exporter import ParquetExporter
from services.forex_service import ForexService
from utils.constants import TORONTO_TZ, Column, Table

if TYPE_CHECKING:
    from .test_types import TempContext

pytestmark = pytest.mark.no_mock_forex


def test_forex_export_parquet(
    temp_ctx: TempContext,
    cached_fx_data: Callable[[str | None], pd.DataFrame],
) -> None:
    """Test FX exports to Parquet with update scenario."""
    with temp_ctx() as ctx:
        config = ctx.config
        parquet_path = config.fx_parquet

        # Setup: Create transactions table with a date 60 days ago
        schema_manager.create_txns_table()
        start_date = (datetime.now(TORONTO_TZ) - timedelta(days=60)).strftime(
            "%Y-%m-%d",
        )
        txn_data = pd.DataFrame(
            {
                Column.Txn.TXN_ID: [1],
                Column.Txn.TICKER: ["MOCK"],
                Column.Txn.TXN_DATE: [start_date],
                Column.Txn.ACTION: ["BUY"],
                Column.Txn.AMOUNT: [1000.0],
                Column.Txn.CURRENCY: ["USD"],
                Column.Txn.PRICE: [100.0],
                Column.Txn.UNITS: [10.0],
                Column.Txn.ACCOUNT: ["TEST"],
            },
        )
        with db.get_connection() as conn:
            txn_data.to_sql(Table.TXNS, conn, if_exists="append", index=False)

        # Test 1: Initial export - should fetch historical data and export to Parquet
        with patch.object(
            ForexService,
            "get_fx_rates_from_boc",
            return_value=cached_fx_data(None),
        ):
            exporter = ParquetExporter()
            result = exporter.export_forex(start_date)

        # Verify Parquet file created with expected data
        assert parquet_path.exists()
        fx_parquet = pd.read_parquet(parquet_path, engine="pyarrow")
        assert len(fx_parquet) >= 40  # 60 days should have ~40 business days
        assert result == len(fx_parquet)

        # Verify dates are within expected range
        fx_dates = pd.to_datetime(fx_parquet[Column.FX.DATE])
        fx_dates = fx_dates.dt.tz_localize(
            TORONTO_TZ,
            ambiguous="NaT",
            nonexistent="shift_forward",
        )
        now_dt = datetime.now(TORONTO_TZ)
        start_dt = pd.to_datetime(start_date).tz_localize(TORONTO_TZ)
        assert fx_dates.max() <= now_dt
        assert fx_dates.min() >= start_dt

        # Test 2: Simulate 10-day gap and update
        # Remove recent FX data to simulate gap
        schema_manager.create_fx_table()
        base_date = datetime.now(TORONTO_TZ) - timedelta(days=40)
        old_fx_dates = [
            (base_date + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(10)
        ]
        old_fx_data = pd.DataFrame(
            {
                Column.FX.DATE: old_fx_dates,
                Column.FX.FXUSDCAD: [1.25 + 0.01 * i for i in range(10)],
                Column.FX.FXCADUSD: [1 / (1.25 + 0.01 * i) for i in range(10)],
            },
        )
        with db.get_connection() as conn:
            # Clear FX table and insert old data
            conn.execute(f"DELETE FROM {Table.FX}")
            old_fx_data.to_sql(Table.FX, conn, if_exists="append", index=False)

        # Export with update scenario - should fetch missing dates
        latest_date_obj = pd.to_datetime(old_fx_dates[-1])
        next_date = (latest_date_obj + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        with patch.object(
            ForexService,
            "get_fx_rates_from_boc",
            return_value=cached_fx_data(next_date),
        ):
            result_update = exporter.export_forex()

        # Verify Parquet updated with all data (including new fetched data)
        fx_parquet_after = pd.read_parquet(parquet_path, engine="pyarrow")
        assert len(fx_parquet_after) >= 20  # Old 10 + new ~10
        assert result_update == len(fx_parquet_after)

        # Verify new dates are present
        fx_dates_after = pd.to_datetime(fx_parquet_after[Column.FX.DATE])
        assert fx_dates_after.max() > latest_date_obj
