"""Ingest Excel data into the application context."""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

import pandas as pd

from app.app_context import get_config
from db import db, preparers
from db.utils import format_transaction_summary
from utils.constants import Table
from utils.logging_setup import get_import_logger

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)
import_logger = get_import_logger()


def import_transactions(folio_path: Path, account: str | None = None) -> int:
    """Import transactions from Excel files and map headers to internal fields.

    Keeps TXN_ESSENTIALS first, then existing DB columns, then net new columns.

    Args:
        folio_path (Path): Path to the Excel file containing transactions.
        account (str | None): Optional account identifier to use as fallback
            when Account column is missing from the Excel file.

    Returns:
        int: Number of transactions imported.
    """
    config = get_config()
    with db.get_connection() as conn:
        existing_count = _get_existing_transaction_count(conn)

    # Log import start with detailed info
    import_logger.info("=" * 60)
    import_logger.info("Starting import from: %s", folio_path)
    import_logger.info("Existing transactions in database: %d", existing_count)

    try:
        txns_df: pd.DataFrame = pd.read_excel(
            folio_path,
            sheet_name=config.transactions_sheet(),
        )
        import_logger.info(
            "Read %d transactions from Excel sheet '%s'",
            len(txns_df),
            config.transactions_sheet(),
        )
    except ValueError:  # pragma: no cover
        error_msg = f"No '{config.transactions_sheet()}' sheet found in {folio_path}."
        import_logger.warning(error_msg)
        import_logger.info("Import completed: 0 transactions imported")
        import_logger.info("=" * 60)
        return 0

    prepared_df: pd.DataFrame = preparers.prepare_transactions(txns_df, account)

    with db.get_connection() as conn:
        try:
            prepared_df.to_sql(Table.TXNS.value, conn, if_exists="append", index=False)
            final_count = _get_existing_transaction_count(conn)
        except sqlite3.IntegrityError:  # pragma: no cover
            _analyze_and_insert_rows(conn, prepared_df)

    txn_count = len(prepared_df)
    msg: str = f"Import completed: {txn_count} transactions imported"
    import_logger.info(msg)
    import_logger.info("Total transactions in database: %d", final_count)
    import_logger.info("=" * 60)

    return txn_count


def _analyze_and_insert_rows(
    conn: db.sqlite3.Connection,
    prepared_df: pd.DataFrame,
) -> None:  # pragma: no cover
    """Analyze and insert rows one by one to identify problematic transactions.

    Args:
        conn: Database connection
        prepared_df: DataFrame with prepared transaction data

    Returns:
        Final count of transactions in database
    """
    analysis_header = "🔍 BULK INSERT FAILED - Analyzing individual transactions..."
    import_logger.error(analysis_header)

    total_rows = len(prepared_df)

    try:
        for idx, (_, row) in enumerate(prepared_df.iterrows(), 1):
            row_df = pd.DataFrame([row])
            row_df.to_sql(
                Table.TXNS.value,
                conn,
                if_exists="append",
                index=False,
            )
            success_msg = f"✅ Row {idx}/{total_rows}: SUCCESS"
            import_logger.info(success_msg)

    except sqlite3.IntegrityError as row_error:
        transaction_summary = format_transaction_summary(row)
        error_msg = f"❌ Row {idx}/{total_rows}: FAILED - {row_error}"
        transaction_msg = f"   {transaction_summary}"
        import_logger.info(error_msg)
        import_logger.info(transaction_msg)
        raise


def _get_existing_transaction_count(conn: db.sqlite3.Connection) -> int:
    """Get the current count of transactions in the database.

    Args:
        conn: Database connection

    Returns:
        Number of existing transactions
    """
    try:
        cursor = conn.cursor()
        query = f'SELECT COUNT(*) FROM "{Table.TXNS.value}"'  # noqa: S608
        cursor.execute(query)
        return cursor.fetchone()[0]
    except sqlite3.OperationalError:
        return 0
