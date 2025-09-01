"""Schema utilities to use with the database and Excel files."""

from __future__ import annotations

import hashlib
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
    3. Filter out duplicate transactions based on synthetic primary key.
    4. Merge with existing Txns schema so prior optional columns are preserved.
    5. Append any new optional columns from Excel to the schema.
    6. Reorder columns: TXN_ESSENTIALS first, then existing optionals, then
    new optionals.

    Args:
        excel_df: DataFrame read from Excel with transaction data.

    Returns:
        DataFrame ready for DB insertion with correct columns and order.

    """
    txn_df: pd.DataFrame = _map_headers_to_internal(excel_df)

    # Filter out duplicate transactions
    original_count = len(txn_df)
    txn_df = _filter_duplicate_transactions(txn_df)
    duplicate_count = original_count - len(txn_df)

    if duplicate_count > 0:
        logger.info("Filtered out %d duplicate transactions", duplicate_count)

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


def _generate_synthetic_key(row: pd.Series) -> str:
    """Generate a synthetic primary key from TXN_ESSENTIAL columns.

    Args:
        row: A pandas Series containing transaction data.

    Returns:
        A hash string representing the synthetic primary key.
    """

    def normalize_value(val: str | float | None) -> str:
        if pd.isna(val):  # pragma: no cover
            return ""
        # Try to treat as float, format to 8 decimals, else as string
        try:
            fval = float(val)
            # Remove trailing zeros and dot if not needed
            return f"{fval:.8f}".rstrip("0").rstrip(".")
        except (ValueError, TypeError):
            return str(val).strip()

    key_parts = [normalize_value(row.get(col, "")) for col in TXN_ESSENTIALS]
    key_string = "|".join(key_parts)
    logger.debug(" syn-key -> %s", key_string)
    return hashlib.sha256(key_string.encode("utf-8")).hexdigest()


def _get_existing_transaction_keys(conn: sqlite3.Connection) -> set[str]:
    """Get synthetic keys for all existing transactions in the database.

    Args:
        conn: Database connection.

    Returns:
        Set of synthetic keys for existing transactions.
    """
    try:
        # Build the query to select TXN_ESSENTIAL columns
        essential_cols = ", ".join(f'"{col}"' for col in TXN_ESSENTIALS)
        query = f'SELECT {essential_cols} FROM "{Table.TXNS.value}"'  # noqa: S608

        existing_df = pd.read_sql_query(query, conn)
        if existing_df.empty:  # pragma: no cover
            return set()

        # Generate synthetic keys for existing transactions
        existing_keys = set()
        logger.debug("Existing transaction keys:")
        for _, row in existing_df.iterrows():
            key = _generate_synthetic_key(row)
            existing_keys.add(key)

    except sqlite3.OperationalError:  # pragma: no cover
        logger.debug(
            "Table '%s' does not exist yet, no existing transactions to check",
            Table.TXNS.value,
        )
        return set()
    else:
        return existing_keys


def _filter_duplicate_transactions(txn_df: pd.DataFrame) -> pd.DataFrame:
    """Filter out transactions that already exist in the database.

    Args:
        txn_df: DataFrame with transaction data.

    Returns:
        DataFrame with duplicate transactions removed.
    """
    if txn_df.empty:  # pragma: no cover
        return txn_df

    with db.get_connection() as conn:
        tables = db.get_tables(conn)
        if Table.TXNS.value not in tables:
            logger.debug("Transaction table does not exist, no duplicates to check")
            return txn_df
        existing_keys = _get_existing_transaction_keys(conn)

    # Empty transaction table exists, with no records.
    if not existing_keys:  # pragma: no cover
        logger.debug(
            "No existing transactions found, proceeding with all %d transactions",
            len(txn_df),
        )
        return txn_df

    # Generate synthetic keys for new transactions
    new_keys = []
    logger.debug("New synthetic keys:")
    for _, row in txn_df.iterrows():
        key = _generate_synthetic_key(row)
        new_keys.append(key)

    # Filter out duplicates
    mask = [key not in existing_keys for key in new_keys]
    filtered_df = txn_df[mask].copy()

    logger.debug(
        "Filtering %d new transactions to %d unique ones",
        len(txn_df),
        len(filtered_df),
    )

    return filtered_df
