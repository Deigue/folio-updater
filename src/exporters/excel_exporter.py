"""Excel exporter module.

Generates Excel folio using Parquet files.
"""

from __future__ import annotations

import logging

import pandas as pd

from app.app_context import get_config
from utils.backup import rolling_backup
from utils.constants import Column

logger = logging.getLogger(__name__)


class ExcelExporter:
    """Handles exporting data from Parquet files to Excel workbook."""

    def __init__(self) -> None:
        """Initialize the Excel exporter."""
        config = get_config()
        self.data_path = config.data_path
        self.folio_path = config.folio_path
        self.tickers_sheet = config.tkr_sheet
        self.txn_sheet = config.txn_sheet
        self.forex_sheet = config.fx_sheet
        self.txn_parquet = config.txn_parquet
        self.fx_parquet = config.fx_parquet
        self.tkr_parquet = config.tkr_parquet

    def generate_excel(self) -> bool:
        """Generate Excel workbook from Parquet files.

        Reads various parquet files from data_path and combines them into a single
        Excel workbook at folio_path.

        Returns:
            bool: True if Excel was successfully generated, False otherwise.
        """
        transactions_df = self._read_transactions()
        if transactions_df is None:  # pragma: no cover
            return False
        forex_df = self._read_forex()
        tickers_df = self._read_tickers()

        if self.folio_path.exists():  # pragma: no cover
            rolling_backup(self.folio_path)

        return self._write_excel(transactions_df, forex_df, tickers_df)

    def _read_transactions(self) -> pd.DataFrame | None:
        """Read transactions from Parquet file."""
        transactions_path = self.txn_parquet

        if not transactions_path.exists():  # pragma: no cover
            logger.error("Transaction parquet file not found: %s", transactions_path)
            return None

        logger.info("Reading Parquet files from %s...", self.data_path)

        try:
            transactions_df = pd.read_parquet(transactions_path, engine="pyarrow")
        except (OSError, ValueError):
            logger.exception("Error reading transactions parquet")
            return None
        else:
            logger.debug("Loaded %d transactions", len(transactions_df))
            return transactions_df

    def _read_forex(self) -> pd.DataFrame:  # pragma: no cover
        """Read forex rates from Parquet file."""
        forex_path = self.fx_parquet

        if not forex_path.exists():
            logger.debug("Forex parquet file not found: %s", forex_path)
            return pd.DataFrame()

        try:
            forex_df = pd.read_parquet(forex_path, engine="pyarrow")
            logger.debug("Loaded %d FX rates", len(forex_df))
        except (OSError, ValueError):
            logger.warning("Error reading forex parquet")
            return pd.DataFrame()
        else:
            return forex_df

    def _read_tickers(self) -> pd.DataFrame:
        """Read tickers from Parquet file."""
        tickers_path = self.tkr_parquet

        if not tickers_path.exists():  # pragma: no cover
            logger.debug("Tickers parquet file not found: %s", tickers_path)
            return pd.DataFrame()

        try:
            tickers_df = pd.read_parquet(tickers_path, engine="pyarrow")
            logger.debug("Loaded %d tickers", len(tickers_df))
        except (OSError, ValueError):
            logger.warning("Error reading tickers parquet")
            return pd.DataFrame()
        else:
            return tickers_df

    def _write_excel(
        self,
        transactions_df: pd.DataFrame,
        forex_df: pd.DataFrame,
        tickers_df: pd.DataFrame,
    ) -> bool:
        """Write DataFrames to Excel workbook."""
        logger.info("Generating Excel workbook at %s...", self.folio_path)
        try:
            with pd.ExcelWriter(
                self.folio_path,
                engine="openpyxl",
            ) as writer:
                transactions_df.to_excel(
                    writer,
                    sheet_name=self.txn_sheet,
                    index=False,
                )

                if not forex_df.empty:  # pragma: no cover
                    forex_df.to_excel(
                        writer,
                        sheet_name=self.forex_sheet,
                        index=False,
                    )

                if not tickers_df.empty:
                    tickers_df.to_excel(
                        writer,
                        sheet_name=self.tickers_sheet,
                        index=False,
                    )
        except (OSError, ValueError):
            logger.exception("Error writing Excel file")
            return False
        else:
            logger.info("=" * 80)
            logger.info("Excel workbook generated successfully:")
            logger.info("  - %d transactions", len(transactions_df))
            logger.info("  - %d FX rates", len(forex_df))
            logger.info("  - %d tickers", len(tickers_df))
            logger.info("  - Output: %s", self.folio_path)
            logger.info("=" * 80)

            return True


def reorder_folio_columns(txn_df: pd.DataFrame) -> pd.DataFrame:  # pragma: no cover
    """Reorder columns for folio export to desired presentation order.

    Desired order: SettleDate, TxnDate, Action, Amount, $, Price, Units, Fee,
                   Account, (blank column), Ticker, (remaining columns)

    Args:
        txn_df: DataFrame with transaction data

    Returns:
        DataFrame with reordered columns
    """
    # Define the desired column order
    desired_order = [
        Column.Txn.SETTLE_DATE,
        Column.Txn.TXN_DATE,
        Column.Txn.ACTION,
        Column.Txn.AMOUNT,
        Column.Txn.CURRENCY,
        Column.Txn.PRICE,
        Column.Txn.UNITS,
        Column.Txn.FEE,
        Column.Txn.ACCOUNT,
        "",  # Blank column for visual separation
        Column.Txn.TICKER,
    ]

    existing_columns = txn_df.columns.tolist()
    for col in existing_columns:
        if col not in desired_order:
            desired_order.append(col)

    result_df = pd.DataFrame()
    for col in desired_order:
        if col == "":
            # Add blank column
            result_df[""] = ""
        elif col in txn_df.columns:
            result_df[col] = txn_df[col]

    return result_df
