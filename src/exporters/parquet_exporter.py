"""Parquet exporter module.

Handles exporting database to Parquet files.
"""

from __future__ import annotations

import logging

import pandas as pd

from app import get_config
from db import fx_rates, txns
from domain import INTERNAL_COLUMNS, Column
from models import txns_frame
from services import FxService

logger = logging.getLogger(__name__)


class ParquetExporter:
    """Handles exporting data from SQLite database to Parquet files."""

    def __init__(self) -> None:
        """Initialize the parquet exporter."""
        config = get_config()
        self.txn_parquet = config.txn_parquet
        self.fx_parquet = config.fx_parquet
        self.tickers_parquet = config.tickers_parquet

    def export_transactions(self) -> int:
        """Export all transactions to Parquet file.

        Returns:
            int: Number of transactions exported.
        """
        stored = sorted(txns.read(), key=lambda txn: txn.txn_id)
        if not stored:  # pragma: no cover
            return 0

        txn_df = self._remove_internal_columns(txns_frame(stored))
        txn_count = len(txn_df)
        logger.debug("Found %d transactions to export...", txn_count)
        txn_df.to_parquet(self.txn_parquet, engine="pyarrow", index=False)
        logger.debug("Exported %d transactions to Parquet", txn_count)
        return txn_count

    def export_fx(self, start_date: str | None = None) -> int:
        """Export all FX rates to Parquet file.

        FX Coverage is extended at both ends, so rates are fetched covering all
        transactions in the folio. Then retrieved from the database and exported.

        Args:
            start_date: Start date in YYYY-MM-DD format. If None, uses earliest
                       transaction date from database.

        Returns:
            int: Number of FX rate records exported.
        """
        FxService.ensure_coverage(start_date)
        rates = fx_rates.read()
        if not rates:  # pragma: no cover
            logger.debug("No FX rates available for export")
            return 0

        fx_count = len(rates)
        pd.DataFrame(
            {
                str(Column.FX.DATE): [rate.date for rate in rates],
                str(Column.FX.FXUSDCAD): [float(rate.usd_cad) for rate in rates],
                str(Column.FX.FXCADUSD): [float(rate.cad_usd) for rate in rates],
            },
        ).to_parquet(self.fx_parquet, engine="pyarrow", index=False)
        logger.debug("Exported %d FX rates to Parquet", fx_count)
        return fx_count

    def export_tickers(self) -> int:
        """Export tickers to Parquet file.

        Returns:
            int: Number of tickers exported.
        """
        tickers = txns.traded_tickers()
        if not tickers:  # pragma: no cover
            return 0

        ticker_count = len(tickers)
        pd.DataFrame({str(Column.Txn.TICKER): tickers}).to_parquet(
            self.tickers_parquet,
            engine="pyarrow",
            index=False,
        )
        logger.debug("Exported %d tickers to Parquet", ticker_count)
        return ticker_count

    def export_all(self) -> tuple[int, int, int]:
        """Export all data to Parquet files.

        Returns:
            tuple: (transactions_count, fx_count, tickers_count)
        """
        txn_count = self.export_transactions()
        fx_count = self.export_fx()
        ticker_count = self.export_tickers()

        logger.info(
            "EXPORTED Parquet files: %d transactions, %d FX rates, %d tickers",
            txn_count,
            fx_count,
            ticker_count,
        )

        return txn_count, fx_count, ticker_count

    def _remove_internal_columns(self, txn_df: pd.DataFrame) -> pd.DataFrame:
        """Remove internal-use-only columns from the DataFrame."""
        return txn_df.drop(columns=list(INTERNAL_COLUMNS), errors="ignore")
