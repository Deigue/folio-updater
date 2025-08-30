"""Ingest Excel data into the application context."""

import logging
from pathlib import Path

import pandas as pd

from app.app_context import get_config
from db import db, schema
from utils.constants import Table

logger = logging.getLogger(__name__)


def import_transactions(folio_path: Path) -> int:
    """Import transactions from Excel files and map headers to internal fields.

    Keeps TXN_ESSENTIALS first, then existing DB columns, then net new columns.

    Args:
        folio_path (Path): Path to the Excel file containing transactions.

    Returns:
        int: Number of transactions imported.
    """
    config = get_config()
    try:
        # TODO: Be able to read any generic Excel file with transactions
        txns_df: pd.DataFrame = pd.read_excel(
            folio_path, sheet_name=config.transactions_sheet()
        )
    except ValueError:
        logger.warning(
            "No '%s' sheet found in %s.",
            config.transactions_sheet(),
            folio_path,
        )
        return 0

    prepared_df: pd.DataFrame = schema.prepare_txns_for_db(txns_df)

    with db.get_connection() as conn:
        prepared_df.to_sql(Table.TXNS.value, conn, if_exists="append", index=False)

    txn_count = len(prepared_df)
    logger.info("Imported %d transactions from Excel into DB.", txn_count)
    return txn_count
