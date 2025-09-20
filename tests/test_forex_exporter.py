"""Tests for Forex exports with new database-first architecture."""

from contextlib import _GeneratorContextManager
from datetime import datetime, timedelta
from typing import Callable
from unittest.mock import patch

import pandas as pd
import pytest

from app.app_context import AppContext
from db import db, schema_manager
from exporters.forex_exporter import ForexExporter
from services.forex_service import ForexService
from utils.constants import TORONTO_TZ, Column, Table

pytestmark = pytest.mark.no_mock_forex


def test_export_full_with_existing_data(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test full FX export with database containing data."""
    with temp_config():
        # Setup database with FX data
        schema_manager.create_fx_table()
        fx_data = pd.DataFrame(
            {
                Column.FX.DATE.value: ["2022-01-03", "2022-01-04", "2022-01-05"],
                Column.FX.FXUSDCAD.value: [1.2635, 1.2658, 1.2701],
                Column.FX.FXCADUSD.value: [1 / 1.2635, 1 / 1.2658, 1 / 1.2701],
            },
        )

        with db.get_connection() as conn:
            fx_data.to_sql(Table.FX.value, conn, if_exists="append", index=False)

        # Mock API call to return empty (no new data needed)
        with patch.object(
            ForexService,
            "get_missing_fx_data",
            return_value=pd.DataFrame(),
        ):
            exporter = ForexExporter()
            result = exporter.export_full("2022-01-03")

            assert result == len(fx_data)


def test_export_full_with_missing_data(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test full FX export when data needs to be fetched from API."""
    with temp_config():
        mock_fx_data = pd.DataFrame(
            {
                Column.FX.DATE.value: ["2022-01-03", "2022-01-04"],
                Column.FX.FXUSDCAD.value: [1.2635, 1.2658],
                Column.FX.FXCADUSD.value: [1 / 1.2635, 1 / 1.2658],
            },
        )

        with patch.object(
            ForexService,
            "get_missing_fx_data",
            return_value=mock_fx_data,
        ):
            exporter = ForexExporter()
            result = exporter.export_full("2022-01-03")

            # Check that the data was inserted into the DB
            with db.get_connection() as conn:
                df = db.get_rows(conn, Table.FX.value)
                assert len(df) == len(mock_fx_data)
                assert set(df[Column.FX.DATE.value]) == {"2022-01-03", "2022-01-04"}
            assert result == len(mock_fx_data)  # Expected 2 records exported


def test_export_update(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test FX export_update only adds missing data to Excel."""
    with temp_config() as ctx:
        schema_manager.create_fx_table()
        fx_data = pd.DataFrame(
            {
                Column.FX.DATE.value: ["2022-01-01", "2022-01-02", "2022-01-03"],
                Column.FX.FXUSDCAD.value: [1.25, 1.26, 1.27],
                Column.FX.FXCADUSD.value: [1 / 1.25, 1 / 1.26, 1 / 1.27],
            },
        )
        # Create minimal folio Excel file required by the app
        folio_path = ctx.config.folio_path
        with pd.ExcelWriter(folio_path, engine="openpyxl") as writer:
            fx_data.to_excel(writer, index=False, sheet_name=ctx.config.forex_sheet())

        with db.get_connection() as conn:
            fx_data.to_sql(Table.FX.value, conn, if_exists="append", index=False)

        # Patch API to return only new data
        new_fx_data = pd.DataFrame(
            {
                Column.FX.DATE.value: ["2022-01-04", "2022-01-05"],
                Column.FX.FXUSDCAD.value: [1.28, 1.29],
                Column.FX.FXCADUSD.value: [1 / 1.28, 1 / 1.29],
            },
        )
        with patch.object(
            ForexService,
            "get_fx_rates_from_boc",
            return_value=new_fx_data,
        ):
            exporter = ForexExporter()
            result = exporter.export_update()

            # Should append only the new rows to the Excel file
            fx_sheet = pd.read_excel(folio_path, sheet_name=ctx.config.forex_sheet())
            assert len(fx_sheet) == len(fx_data) + len(new_fx_data)
            assert set(fx_sheet[Column.FX.DATE.value]) == {
                "2022-01-01",
                "2022-01-02",
                "2022-01-03",
                "2022-01-04",
                "2022-01-05",
            }
            assert result == len(new_fx_data)  # Only 2 new records exported


def test_export_full_real_api(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test full FX export with real API call for last 2 months."""
    with temp_config() as ctx:
        folio_path = ctx.config.folio_path
        # Create minimal Excel file with required sheets
        with pd.ExcelWriter(folio_path, engine="openpyxl") as writer:
            pd.DataFrame({Column.Ticker.TICKER: ["MOCK"]}).to_excel(
                writer,
                index=False,
                sheet_name=ctx.config.tickers_sheet(),
            )
            pd.DataFrame(
                {
                    Column.Txn.TICKER.value: ["MOCK"],
                    Column.Txn.TXN_DATE.value: [
                        (datetime.now(TORONTO_TZ) - timedelta(days=60)).strftime(
                            "%Y-%m-%d",
                        ),
                    ],
                },
            ).to_excel(writer, index=False, sheet_name=ctx.config.transactions_sheet())

        start_date = (datetime.now(TORONTO_TZ) - timedelta(days=60)).strftime(
            "%Y-%m-%d",
        )
        exporter = ForexExporter()
        result = exporter.export_full(start_date)
        fx_sheet = pd.read_excel(folio_path, sheet_name=ctx.config.forex_sheet())
        assert len(fx_sheet) >= 40  # noqa: PLR2004
        # Ensure all FX dates are within the expected range (tz-aware)
        fx_dates = pd.to_datetime(fx_sheet[Column.FX.DATE.value])
        fx_dates = fx_dates.dt.tz_localize(
            TORONTO_TZ,
            ambiguous="NaT",
            nonexistent="shift_forward",
        )
        start_dt = pd.to_datetime(start_date).tz_localize(TORONTO_TZ)
        now_dt = datetime.now(TORONTO_TZ)
        assert fx_dates.min() >= start_dt
        assert fx_dates.max() <= now_dt
        assert result == len(fx_sheet)


def test_export_update_real_api(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test FX export_update with real API call after inserting dummy data."""
    with temp_config() as ctx:
        schema_manager.create_fx_table()
        # Insert FX data for 10 days, ending 30 days ago
        base_date = datetime.now(TORONTO_TZ) - timedelta(days=40)
        fx_dates = [
            (base_date + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(10)
        ]
        fx_data = pd.DataFrame(
            {
                Column.FX.DATE.value: fx_dates,
                Column.FX.FXUSDCAD.value: [1.25 + 0.01 * i for i in range(10)],
                Column.FX.FXCADUSD.value: [1 / (1.25 + 0.01 * i) for i in range(10)],
            },
        )
        folio_path = ctx.config.folio_path
        # Create minimal Excel file with initial FX data
        with pd.ExcelWriter(folio_path, engine="openpyxl") as writer:
            pd.DataFrame(
                {
                    Column.Txn.TICKER.value: ["MOCK"],
                    Column.Txn.TXN_DATE.value: [fx_dates[0]],
                },
            ).to_excel(writer, index=False, sheet_name=ctx.config.transactions_sheet())
            fx_data.to_excel(writer, index=False, sheet_name=ctx.config.forex_sheet())

        with db.get_connection() as conn:
            fx_data.to_sql(Table.FX.value, conn, if_exists="append", index=False)

        exporter = ForexExporter()
        result = exporter.export_update()
        fx_sheet = pd.read_excel(
            ctx.config.folio_path,
            sheet_name=ctx.config.forex_sheet(),
        )
        assert len(fx_sheet) > len(fx_data)
        assert set(fx_dates).issubset(set(fx_sheet[Column.FX.DATE.value]))
        fx_dates_all = pd.to_datetime(fx_sheet[Column.FX.DATE.value])
        fx_dates_all = fx_dates_all.dt.tz_localize(
            TORONTO_TZ,
            ambiguous="NaT",
            nonexistent="shift_forward",
        )
        min_dt = pd.to_datetime(fx_dates[0]).tz_localize(TORONTO_TZ)
        now_dt = datetime.now(TORONTO_TZ)
        assert fx_dates_all.min() == min_dt
        assert fx_dates_all.max() <= now_dt
        assert result == len(fx_sheet) - len(fx_data)
