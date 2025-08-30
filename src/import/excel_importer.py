"""
Ingest Excel data into the application context.
"""

import logging

import pandas as pd

from db import db, schema

logger = logging.getLogger(__name__)


def import_transactions(folio_path) -> int:
    """
    Imports transactions from Excel files. Maps headers to internal fields.
    Keeps TXN_ESSENTIALS first, then existing DB columns, then net new columns.

    Args:
        folio_path (str): Path to the Excel file containing transactions.

    Returns:
        int: Number of transactions imported.
    """
    try:
        # TODO: Be able to read any generic Excel file with transactions
        # TODO: Configurable Txns sheet name via config.SHEETS
        df_txns = pd.read_excel(folio_path, sheet_name="Txns")
    except ValueError:
        logger.warning("No 'Txns' sheet found in Excel.")
        return 0

    df_ready = schema.prepare_txns_for_db(df_txns)

    with db.get_connection() as conn:
        # TODO: Utilize constant from config for Txns table
        df_ready.to_sql("Txns", conn, if_exists="append", index=False)

    txn_count = len(df_ready)
    logger.info("Imported %d transactions from Excel into DB.", txn_count)
    return txn_count
