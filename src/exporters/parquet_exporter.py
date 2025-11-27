"""Parquet exporter module.

Handles exporting database to Parquet files.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.app_context import get_config
from utils.constants import Column, Table
from db import get_connection, get_distinct_values, get_rows
from services import ForexService

if TYPE_CHECKING:
    import pandas as pd

logger = logging.getLogger(__name__)


class ParquetExporter:
    """Handles exporting data from SQLite database to Parquet files."""

    def __init__(self) -> None:
        """Initialize the parquet exporter."""
        config = get_config()
        self.data_path = config.data_path
        self.txn_parquet = config.txn_parquet
        self.fx_parquet = config.fx_parquet
        self.tkr_parquet = config.tkr_parquet

    def export_transactions(self) -> int:
        """Export all transactions to Parquet file.

        Returns:
            int: Number of transactions exported.
        """
        with get_connection() as conn:
            txn_df = get_rows(conn, Table.TXNS)

        if txn_df.empty:  # pragma: no cover
            return 0

        txn_df = self._remove_internal_columns(txn_df)
        txn_count = len(txn_df)
        logger.debug("Found %d transactions to export...", txn_count)
        txn_df.to_parquet(self.txn_parquet, engine="fastparquet", index=False)
        logger.debug("Exported %d transactions to Parquet", txn_count)
        return txn_count

    def export_forex(self, start_date: str | None = None) -> int:
        """Export all FX rates to Parquet file.

        This method:
        1. Checks database for existing FX rates
        2. Fetches missing rates from API if needed
        3. Stores new rates in database
        4. Exports all rates from database to Parquet

        Args:
            start_date: Start date in YYYY-MM-DD format. If None, uses earliest
                       transaction date from database.

        Returns:
            int: Number of FX rate records exported.
        """
        self._ensure_fx_data_current(start_date)
        fx_df = ForexService.get_fx_rates_from_db()
        if fx_df.empty:  # pragma: no cover
            logger.debug("No FX rates available for export")
            return 0

        fx_count = len(fx_df)
        fx_df.to_parquet(self.fx_parquet, engine="fastparquet", index=False)
        logger.debug("Exported %d FX rates to Parquet", fx_count)
        return fx_count

    def export_tickers(self) -> int:
        """Export tickers to Parquet file.

        Returns:
            int: Number of tickers exported.
        """
        with get_connection() as conn:
            filter_condition = (
                f'"{Column.Txn.TICKER}" IS NOT NULL AND "{Column.Txn.TICKER}" != \'\''
            )
            tickers_df = get_distinct_values(
                conn,
                Table.TXNS,
                Column.Txn.TICKER,
                filter_condition=filter_condition,
                order_by=f'"{Column.Txn.TICKER}"',
            )

        if tickers_df.empty:  # pragma: no cover
            return 0

        ticker_count = len(tickers_df)
        tickers_df.to_parquet(self.tkr_parquet, engine="fastparquet", index=False)
        logger.debug("Exported %d tickers to Parquet", ticker_count)
        return ticker_count

    def export_all(self) -> tuple[int, int, int]:
        """Export all data to Parquet files.

        Returns:
            tuple: (transactions_count, forex_count, tickers_count)
        """
        txn_count = self.export_transactions()
        fx_count = self.export_forex()
        ticker_count = self.export_tickers()

        logger.info(
            "Parquet export completed: %d transactions, %d FX rates, %d tickers",
            txn_count,
            fx_count,
            ticker_count,
        )

        return txn_count, fx_count, ticker_count

    def _ensure_fx_data_current(self, start_date: str | None = None) -> None:
        """Ensure FX data in database is current.

        Args:
            start_date: Optional start date for initial data fetch.
        """
        if ForexService.is_fx_data_current():  # pragma: no cover
            return
        fx_df = ForexService.get_missing_fx_data(start_date)
        ForexService.insert_fx_data(fx_df)

    def _remove_internal_columns(self, txn_df: pd.DataFrame) -> pd.DataFrame:
        """Remove internal-use-only columns from the DataFrame."""
        internal_cols = [
            Column.Txn.TXN_ID,
        ]
        return txn_df.drop(columns=internal_cols, errors="ignore")
