"""
Schema utilities to use with the database and Excel files.
"""

import logging
import re
import pandas as pd

from src.constants import TXN_ESSENTIALS
from src import config

logger = logging.getLogger(__name__)


def _normalize(name: str) -> str:
    name = name.strip().lower()
    if not name:
        return name
    return re.sub(r"[^a-z0-9$]", "", name)

def prepare_txns_for_db(excel_df: pd.DataFrame):
    """
    Transform a transaction DataFrame from Excel to match the current
    Txns schema.
    Steps:
    1. Map Excel headers to internal transaction fields using HEADER_KEYWORDS.
    2. Ensure all TXN_ESSENTIALS are present.
    3. Merge with existing Txns schema so prior optional columns are preserved.
    4. Append any new optional columns from Excel to the schema.
    5. Reorder columns: TXN_ESSENTIALS first, then existing optionals, then
    new optionals.

    Args:
        excel_df: DataFrame read from Excel with transaction data.

    Returns:
        DataFrame ready for DB insertion with correct columns and order.
    """
    return NotImplementedError

def map_headers(excel_headers):
    """
    Map Excel headers to internal TXN_ESSENTIALS keys using keywords from config.
    Returns mapping dict: {internal_name: excel_header}.
    Raises ValueError if any essential internal name cannot be mapped.
    """
    header_keywords = config.HEADER_KEYWORDS

    norm_keywords: dict[str, set[str]] = {
        internal: {_normalize(k) for k in keywords}
        for internal, keywords in header_keywords.items()
    }

    mapped = {}
    unmatched = set(TXN_ESSENTIALS) # make a copy

    for column in excel_headers:
        norm_column = _normalize(column)
        for internal, keywords in norm_keywords.items():
            if norm_column in keywords and internal in unmatched:
                mapped[internal] = column
                unmatched.remove(internal)
                break # next column

    if unmatched:
        error_msg = f"Could not map essential columns: {unmatched}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    logger.debug("Header mapping result: %s", mapped)
    return mapped
