"""Excel exporter module.

Handles export tasks from the internal database to Excel files.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd
from openpyxl import load_workbook

from app.app_context import get_config
from db import db
from db.utils import format_transaction_summary
from utils.backup import rolling_backup
from utils.constants import Column, Table

if TYPE_CHECKING:
    from pathlib import Path


logger = logging.getLogger(__name__)


class TransactionExporter:
    """Handles exporting transactions from SQLite database to Excel sheets."""

    def __init__(self) -> None:
        """Initialize the transaction exporter."""
        config = get_config()
        self.folio_path = config.folio_path
        self.txn_sheet = config.transactions_sheet()

    def export_full(self) -> int:
        """Perform a full export of all transactions to Excel.

        This completely replaces the transactions sheet (if exists) with transactions
        from the database.

        Returns:
            int: Number of transactions exported.
        """
        with db.get_connection() as conn:
            transactions_df = db.get_rows(conn, Table.TXNS.value)

        if transactions_df.empty:  # pragma: no cover
            return 0

        transactions_df = remove_internal_columns(transactions_df)
        transaction_count = len(transactions_df)
        logger.debug("Found %d transactions to export...", transaction_count)
        if self.folio_path.exists():
            mode = "a"
            sheet_exists = "replace"
            rolling_backup(self.folio_path)
            check_file_read_write_access(self.folio_path)
        else:  # pragma: no cover
            mode = "w"
            sheet_exists = None

        with pd.ExcelWriter(
            self.folio_path,
            engine="openpyxl",
            mode=mode,
            if_sheet_exists=sheet_exists,
        ) as writer:
            transactions_df.to_excel(
                writer,
                sheet_name=self.txn_sheet,
                index=False,
            )

        logger.info("=" * 60)
        logger.info(
            "Full export completed: %d transactions exported",
            transaction_count,
        )
        logger.info("=" * 60)

        return transaction_count

    def export_update(self) -> int:
        """Perform an incremental export of new transactions to Excel.

        This only adds new transactions from the database having a date equal or greater
        than the latest date acquired from the Excel sheet.

        Returns:
            int: Number of new transactions exported.

        Raises:
            FileNotFoundError: If the Excel file doesn't exist.
            ValueError: If there's an issue with reading the sheet.
        """
        if not self.folio_path.exists():  # pragma: no cover
            msg = f"Excel file not found: {self.folio_path}"
            logger.error(msg)
            raise FileNotFoundError(msg)

        try:
            excel_df = pd.read_excel(
                self.folio_path,
                sheet_name=self.txn_sheet,
                engine="openpyxl",
            )
        except ValueError as e:  # pragma: no cover
            msg = f"Error reading sheet '{self.txn_sheet}': {e}"
            logger.exception(msg)
            raise ValueError(msg) from e

        with db.get_connection() as conn:
            db_df = db.get_rows(conn, Table.TXNS.value)

        if db_df.empty or excel_df.empty:
            return 0

        logger.debug("Starting incremental export...")
        logger.debug("Found %d transactions in database", len(db_df))
        logger.debug("Found %d transactions in Excel sheet", len(excel_df))

        # Remove internal columns (ID) - folio is for reporting, not re-import
        db_df = remove_internal_columns(db_df)
        new_transactions_df = self._find_new_transactions(excel_df, db_df)
        if new_transactions_df.empty:
            return 0
        txn_count = len(new_transactions_df)
        msg = f"Found {txn_count} new transactions to export"
        logger.info(msg)

        excel_cols = excel_df.columns.tolist()

        rolling_backup(self.folio_path)
        check_file_read_write_access(self.folio_path)
        workbook = load_workbook(self.folio_path)
        worksheet = workbook[self.txn_sheet]
        new_transactions_df = new_transactions_df[excel_cols]
        for _, row in new_transactions_df.iterrows():
            worksheet.append(row.tolist())
        workbook.save(self.folio_path)
        workbook.close()
        logger.info("=" * 60)
        logger.info(
            "Update export completed: %d new transactions exported",
            txn_count,
        )
        logger.info("=" * 60)
        return txn_count

    def _find_new_transactions(
        self,
        excel_df: pd.DataFrame,
        db_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """Find new transactions from the database based on the transaction date.

        Args:
            excel_df: DataFrame with existing Excel transactions.
            db_df: DataFrame with database transactions.

        Returns:
            DataFrame containing only new transactions.
        """
        # Assumes that excel_df is reliably formatted since this is meant to be called
        # after a prior full export.
        txn_date_col = Column.Txn.TXN_DATE.value
        latest_date = excel_df[txn_date_col].max()
        logger.debug("Latest transaction date in Excel: %s", latest_date)
        new_df = db_df[db_df[txn_date_col] > latest_date].copy()

        # Deduplicate common transactions on latest date to get unique ones
        db_latest = db_df[db_df[txn_date_col] == latest_date].copy()
        excel_latest = excel_df[excel_df[txn_date_col] == latest_date].copy()
        if db_latest.empty:  # pragma: no cover
            return new_df
        excel_keys = set(excel_latest.apply(format_transaction_summary, axis=1))
        db_keys = db_latest.apply(format_transaction_summary, axis=1)
        mask = ~db_keys.isin(excel_keys)
        unique_txns = db_latest[mask].copy()

        if not unique_txns.empty:
            new_df = pd.concat([new_df, unique_txns], ignore_index=True)

        return new_df


def check_file_read_write_access(path: Path) -> None:
    """Raise an exception if the file is not accessible for both reading and writing."""
    try:
        with path.open("rb+"):
            return
    except PermissionError as e:  # pragma: no cover
        msg = f"File '{path}' is not accessible for reading and writing: {e}"
        logger.exception(msg)
        raise


def remove_internal_columns(txn_df: pd.DataFrame) -> pd.DataFrame:
    """Remove internal-use-only columns from the DataFrame."""
    internal_cols = [
        Column.Txn.TXN_ID.value,
    ]
    return txn_df.drop(columns=internal_cols, errors="ignore")


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
        Column.Txn.SETTLE_DATE.value,
        Column.Txn.TXN_DATE.value,
        Column.Txn.ACTION.value,
        Column.Txn.AMOUNT.value,
        Column.Txn.CURRENCY.value,
        Column.Txn.PRICE.value,
        Column.Txn.UNITS.value,
        Column.Txn.FEE.value,
        Column.Txn.ACCOUNT.value,
        "",  # Blank column for visual separation
        Column.Txn.TICKER.value,
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
