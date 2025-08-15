"""
Ingest Excel data into the application context.
"""
import pandas as pd
import logging
from . import db, schema
from src.constants import TXN_ESSENTIALS

logger = logging.getLogger(__name__)

def import_transactions(folio_path) -> int:
    """
    Imports transactions from Excel files. Maps headers to internal fields.
    Keeps TXN_ESSENTIALS first, then existing DB columns, then net new columns.
    """
    try:
        # TODO: Be able to read any generic Excel file with transactions
        # TODO: Configurable Txns sheet name via config.SHEETS
        df_txns = pd.read_excel(folio_path, sheet_name="Txns")
    except ValueError:
        logger.warning("No 'Txns' sheet found in Excel.")
        return 0

    schema.map

    # Determine mapping from internal_name -> actual Excel header
    mapping = schema.map_headers(df_txns.columns)
    # Reverse mapping to rename DataFrame columns to internal names
    rename_map = { excel_col: internal for internal, excel_col in mapping.items() }

    df_internal = df_txns.rename(columns=rename_map)

    # Ensure all essential columns are present and ordered
    df_internal = df_internal[TXN_ESSENTIALS]

    with db.get_connection() as conn:
        df_internal.to_sql("Txns", conn, if_exists="append", index=False)

    logger.info("Imported %d transactions from Excel into DB.", len(df_internal))
    return len(df_internal)

