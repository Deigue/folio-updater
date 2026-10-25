"""Tests for Forex exports with Parquet storage."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from db import create_txns_table, get_connection
from domain import TORONTO_TZ, Column, Table
from exporters import ParquetExporter

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from .helpers.context import TempContext


def _seed_usd_buy(days_ago: int) -> str:
    """Seed one USD buy, so the folio needs FX from its trade date on.

    Returns:
        The trade date, YYYY-MM-DD.
    """
    create_txns_table()
    trade_date = (datetime.now(TORONTO_TZ) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%d",
    )
    txn_data = pd.DataFrame(
        {
            Column.Txn.TXN_ID: [1],
            Column.Txn.TICKER: ["MOCK"],
            Column.Txn.TXN_DATE: [trade_date],
            Column.Txn.ACTION: ["BUY"],
            Column.Txn.AMOUNT: [1000.0],
            Column.Txn.CURRENCY: ["USD"],
            Column.Txn.PRICE: [100.0],
            Column.Txn.UNITS: [10.0],
            Column.Txn.ACCOUNT: ["TEST"],
        },
    )
    with get_connection() as conn:
        txn_data.to_sql(Table.TXNS, conn, if_exists="append", index=False)
    return trade_date


def test_forex_export_fetches_from_the_start_date(
    temp_ctx: TempContext,
    boc_fetch: MagicMock,
) -> None:
    with temp_ctx() as ctx:
        parquet_path = ctx.config.fx_parquet
        start_date = _seed_usd_buy(days_ago=60)

        result = ParquetExporter().export_forex(start_date)

        boc_fetch.assert_called_once_with(start_date)

        assert parquet_path.exists()
        fx_parquet = pd.read_parquet(parquet_path, engine="fastparquet")
        assert len(fx_parquet) >= 35  # 60 days less weekends and stat holidays
        assert result == len(fx_parquet)

        fx_dates = pd.to_datetime(fx_parquet[Column.FX.DATE])
        fx_dates = fx_dates.dt.tz_localize(
            TORONTO_TZ,
            ambiguous="NaT",
            nonexistent="shift_forward",
        )
        start_dt = pd.to_datetime(start_date).tz_localize(TORONTO_TZ)
        assert fx_dates.max() <= datetime.now(TORONTO_TZ)
        assert fx_dates.min() >= start_dt


def test_forex_export_tops_up_after_the_latest_rate(
    temp_ctx: TempContext,
    boc_fetch: MagicMock,
) -> None:
    """Rates on hand stop 30 days ago, so only the days after them are fetched."""
    with temp_ctx() as ctx:
        parquet_path = ctx.config.fx_parquet
        _seed_usd_buy(days_ago=40)
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
        with get_connection() as conn:
            old_fx_data.to_sql(Table.FX, conn, if_exists="append", index=False)

        result = ParquetExporter().export_forex()

        latest_date_obj = pd.to_datetime(old_fx_dates[-1])
        next_date = (latest_date_obj + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        boc_fetch.assert_called_once_with(next_date)
        fx_parquet = pd.read_parquet(parquet_path, engine="fastparquet")
        assert len(fx_parquet) >= 20  # Old 10 + new ~20
        assert result == len(fx_parquet)
        assert pd.to_datetime(fx_parquet[Column.FX.DATE]).max() > latest_date_obj
