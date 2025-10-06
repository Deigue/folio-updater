"""Tests for Forex exports with new database-first architecture."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Callable
from unittest.mock import patch

import pandas as pd
import pytest

from db import db, schema_manager
from exporters.forex_exporter import ForexExporter
from services.forex_service import ForexService
from utils.constants import TORONTO_TZ, Column, Table

if TYPE_CHECKING:
    from contextlib import _GeneratorContextManager

    from app.app_context import AppContext

pytestmark = pytest.mark.no_mock_forex


@pytest.mark.parametrize(
    ("method", "expected_min_rows"),
    [
        ("full", 40),  # 60 days should have ~40 business days
        ("update", 10),  # After 10 day gap, expect ~10 business days
    ],
)
def test_forex_api_calls(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
    cached_fx_data: Callable[[str | None], pd.DataFrame],
    method: str,
    expected_min_rows: int,
) -> None:
    """Test FX exports with cached real API data (single API call per session)."""
    with temp_config() as ctx:
        folio_path = ctx.config.folio_path

        if method == "full":
            with pd.ExcelWriter(folio_path, engine="openpyxl") as writer:
                pd.DataFrame({Column.Ticker.TICKER: ["MOCK"]}).to_excel(
                    writer,
                    index=False,
                    sheet_name=ctx.config.tickers_sheet(),
                )
                pd.DataFrame(
                    {
                        Column.Txn.TICKER: ["MOCK"],
                        Column.Txn.TXN_DATE: [
                            (datetime.now(TORONTO_TZ) - timedelta(days=60)).strftime(
                                "%Y-%m-%d",
                            ),
                        ],
                    },
                ).to_excel(
                    writer,
                    index=False,
                    sheet_name=ctx.config.transactions_sheet(),
                )

            start_date = (datetime.now(TORONTO_TZ) - timedelta(days=60)).strftime(
                "%Y-%m-%d",
            )

            with patch.object(
                ForexService,
                "get_fx_rates_from_boc",
                return_value=cached_fx_data(None),
            ):
                exporter = ForexExporter()
                result = exporter.export_full(start_date)

        else:  # update
            schema_manager.create_fx_table()
            base_date = datetime.now(TORONTO_TZ) - timedelta(days=40)
            fx_dates = [
                (base_date + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(10)
            ]
            fx_data = pd.DataFrame(
                {
                    Column.FX.DATE: fx_dates,
                    Column.FX.FXUSDCAD: [1.25 + 0.01 * i for i in range(10)],
                    Column.FX.FXCADUSD: [1 / (1.25 + 0.01 * i) for i in range(10)],
                },
            )
            with pd.ExcelWriter(folio_path, engine="openpyxl") as writer:
                pd.DataFrame(
                    {
                        Column.Txn.TICKER: ["MOCK"],
                        Column.Txn.TXN_DATE: [fx_dates[0]],
                    },
                ).to_excel(
                    writer,
                    index=False,
                    sheet_name=ctx.config.transactions_sheet(),
                )
                fx_data.to_excel(
                    writer,
                    index=False,
                    sheet_name=ctx.config.forex_sheet(),
                )

            with db.get_connection() as conn:
                fx_data.to_sql(Table.FX, conn, if_exists="append", index=False)

            latest_date_obj = pd.to_datetime(fx_dates[-1])
            next_date = (latest_date_obj + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            with patch.object(
                ForexService,
                "get_fx_rates_from_boc",
                return_value=cached_fx_data(next_date),
            ):
                exporter = ForexExporter()
                result = exporter.export_update()

        # Verify results
        fx_sheet = pd.read_excel(folio_path, sheet_name=ctx.config.forex_sheet())
        assert len(fx_sheet) >= expected_min_rows

        fx_dates_all = pd.to_datetime(fx_sheet[Column.FX.DATE])
        fx_dates_all = fx_dates_all.dt.tz_localize(
            TORONTO_TZ,
            ambiguous="NaT",
            nonexistent="shift_forward",
        )

        now_dt = datetime.now(TORONTO_TZ)
        assert fx_dates_all.max() <= now_dt

        if method == "full":
            start_dt = pd.to_datetime(
                (datetime.now(TORONTO_TZ) - timedelta(days=60)).strftime("%Y-%m-%d"),
            ).tz_localize(TORONTO_TZ)
            assert fx_dates_all.min() >= start_dt
            assert result == len(fx_sheet)
        else:  # update
            assert result == len(fx_sheet) - 10  # Minus initial data
