"""
Schema utilities to use with the database and Excel files.
"""


import logging
import re
import pandas as pd

from src.constants import TXN_ESSENTIALS
from src import config, db

logger = logging.getLogger(__name__)


def _normalize(name: str) -> str:
    name = name.strip().lower()
    if not name:
        return name
    return re.sub(r"[^a-z0-9$]", "", name)

# TODO Test this function for different edge cases
def prepare_txns_for_db(excel_df: pd.DataFrame) -> pd.DataFrame:
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
    header_keywords = config.HEADER_KEYWORDS

    # Normalize keywords for matching
    norm_keywords: dict[str, set[str]] = {
        internal: { _normalize(kw) for kw in keywords }
        for internal, keywords in header_keywords.items()
    }

    # Match internal fields to Excel columns
    mapping: dict[str, str] ={}
    unmatched = set(TXN_ESSENTIALS) # copy of essential fields to match

    for column in excel_df.columns:
        normalized_column = _normalize(column)
        for internal, keywords in norm_keywords.items():
            if normalized_column in keywords and internal in unmatched:
                mapping[column] = internal
                unmatched.remove(internal)
                break

    logger.debug("Excel->internal mappings: %s", mapping)
    # Ensure TXN_ESSENTIALS are present in the mapping
    if unmatched:
        error_message = f"Could not map essential columns: {unmatched}"
        logger.error(error_message)
        raise ValueError(error_message)

    # Merge with existing Txns schema

    # Map headers to internal names
    df_internal = excel_df.rename(columns=mapping)

    # TODO: Replace with config internal name Txns.
    existing_columns = db.get_columns(db.get_connection(), "Txns")

    # Column ordering template
    if existing_columns:
        final_columns = existing_columns + [
            col for col in df_internal.columns if col not in existing_columns
        ]
    else:
        final_columns = list(TXN_ESSENTIALS) + [
            col for col in df_internal.columns if col not in TXN_ESSENTIALS
        ]
    logger.debug("Final ordered columns: %s", final_columns)

    # Fill extraneous columns with NA
    for col in final_columns:
        if col not in df_internal.columns:
            df_internal[col] = pd.NA

    # Enforce reordering
    df_internal = df_internal[final_columns]

    return df_internal
