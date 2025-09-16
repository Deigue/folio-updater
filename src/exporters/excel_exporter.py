"""Excel exporter module.

Handles export tasks from the internal database to Excel files.
"""

import logging
import pandas as pd
from app.app_context import get_config
from db import db
from utils.constants import Column, Table

logger = logging.getLogger(__name__)


class TransactionExporter:
    """Handles exporting transactions from SQLite database to Excel sheets."""

    def __init__(self) -> None:
        """Initialize the transaction exporter."""
        config = get_config()
        self.folio_path = config.folio_path
        self.sheet_name = config.transactions_sheet()

    def export_full(self) -> int:
        """Perform a full export of all transactions to Excel.

        This completely replaces the transactions sheet (if exists) with transactions
        from the database.

        Returns:
            int: Number of transactions exported.

        Raises:
            FileNotFoundError: If the Excel file doesn't exist.
        """
        if not self.folio_path.exists():  # pragma: no cover
            msg = f"Excel file not found: {self.folio_path}"
            logger.error(msg)
            raise FileNotFoundError(msg)

        with db.get_connection() as conn:
            transactions_df = db.get_rows(conn, Table.TXNS.value)

        if transactions_df.empty:
            return 0

        transactions_df = transactions_df.drop(
            columns=[Column.Txn.TXN_ID.value],
            errors="ignore",
        )
        transaction_count = len(transactions_df)
        logger.debug("Found %d transactions to export...", transaction_count)

        with pd.ExcelWriter(
            self.folio_path,
            engine="openpyxl",
            mode="a",
            if_sheet_exists="replace",
        ) as writer:
            transactions_df.to_excel(
                writer,
                sheet_name=self.sheet_name,
                index=False,
            )
        # TODO@deigue: verify checkpoint

        logger.info(
            "Transaction full export completed: %d transactions exported",
            transaction_count,
        )

        return transaction_count


        Args:
            folio_path: Optional path to the Excel file. If None, uses config.
        """
        config = get_config()
        self.folio_path = folio_path or config.folio_path
        self.sheet_name = config.transactions_sheet()

