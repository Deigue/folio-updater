"""Schema utilities to use with the database and Excel files."""

from __future__ import annotations

import logging
import re
import sqlite3

import pandas as pd

from app.app_context import get_config
from db import db
from utils.constants import TXN_ESSENTIALS, Table

logger = logging.getLogger(__name__)


def prepare_txns_for_db(excel_df: pd.DataFrame) -> pd.DataFrame:
    """Transform a transaction DataFrame from Excel to match the current Txns schema.

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
    txn_df: pd.DataFrame = _map_headers_to_internal(excel_df)
    with db.get_connection() as conn:
        existing_columns = db.get_columns(conn, Table.TXNS.value)

    # Column ordering template
    if existing_columns:
        new_columns = [col for col in txn_df.columns if col not in existing_columns]
        final_columns = existing_columns + new_columns
        _add_missing_columns(new_columns)
    else:
        final_columns = list(TXN_ESSENTIALS) + [
            col for col in txn_df.columns if col not in TXN_ESSENTIALS
        ]
    logger.debug("Final ordered columns: %s", final_columns)
    for col in final_columns:
        if col not in txn_df.columns:
            txn_df[col] = pd.NA
    return txn_df[final_columns]


def _normalize(name: str) -> str:
    """Normalize string for the database.

    Normalizes the inputstring by converting to lowercase and removing non-alphanumeric
    characters. (except $)

    Args:
        name: The input string to normalize

    Returns:
        str: The normalized string, or empty string if applicable

    Example:
        >>> _normalize("  Transaction Date  ")
        'transactiondate'
    """
    name = name.strip().lower()
    if not name:  # pragma: no cover
        return name
    return re.sub(r"[^a-z0-9$]", "", name)


def _add_missing_columns(columns: list[str]) -> None:
    """Add missing columns to the table."""
    if not columns:
        return
    for c in columns:
        alter_sql = f'ALTER TABLE "{Table.TXNS.value}" ADD COLUMN "{c}" TEXT'
        with db.get_connection() as conn:
            try:
                conn.execute(alter_sql)
                logger.debug("Added new column '%s' to table '%s'", c, Table.TXNS.value)
            except sqlite3.OperationalError as e:  # pragma: no cover
                logger.warning("Could not add column '%s': %s", c, e)


def _map_headers_to_internal(excel_df: pd.DataFrame) -> pd.DataFrame:
    """Map DataFrame columns from Excel headers to internal names."""
    config = get_config()
    header_keywords = config.header_keywords

    norm_keywords: dict[str, set[str]] = {
        internal: {_normalize(kw) for kw in keywords}
        for internal, keywords in header_keywords.items()
    }

    mapping: dict[str, str] = {}
    unmatched = set(TXN_ESSENTIALS)  # copy of essential fields to match

    for column in excel_df.columns:
        normalized_column = _normalize(column)
        for internal, keywords in norm_keywords.items():
            if normalized_column in keywords and internal in unmatched:
                mapping[column] = internal
                unmatched.remove(internal)
                break

    if mapping:
        pretty_mapping = "\n".join(f'"{k}" -> "{v}"' for k, v in mapping.items())
        logger.debug("Excel->internal mappings:\n%s", pretty_mapping)
    else:
        logger.debug("Excel->internal mappings: {}")  # pragma: no cover

    # Ensure TXN_ESSENTIALS are present in the mapping
    if unmatched:
        error_message = f"Could not map essential columns: {unmatched}"
        logger.error(error_message)
        raise ValueError(error_message)

    # Map headers to internal names
    return excel_df.rename(columns=mapping)
