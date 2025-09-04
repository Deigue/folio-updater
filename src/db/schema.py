"""Schema utilities to use with the database and Excel files."""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3

import pandas as pd

from app.app_context import get_config
from db import db, schema_manager
from utils.constants import TXN_ESSENTIALS, Table
from utils.logging_setup import get_import_logger

logger = logging.getLogger(__name__)
import_logger = get_import_logger()


def prepare_txns_for_db(excel_df: pd.DataFrame) -> pd.DataFrame:
    """Transform a transaction DataFrame from Excel to match the current Txns schema.

    Steps:
    1. Map Excel headers to internal transaction fields using HEADER_KEYWORDS.
    2. Ensure all TXN_ESSENTIALS are present.
    3. Filter out duplicate transactions within the import itself.
    4. Filter out duplicate transactions already in DB.
    5. Merge with existing Txns schema so prior optional columns are preserved.
    6. Append any new optional columns from Excel to the schema.
    7. Reorder: TXN_ESSENTIALS -> existing optionals -> new optionals.

    Args:
        excel_df: DataFrame read from Excel with transaction data.

    Returns:
        DataFrame ready for DB insertion with correct columns and order.

    """
    if excel_df.empty:  # pragma: no cover
        return excel_df

    schema_manager.create_txns_table()
    txn_df = _map_headers_to_internal(excel_df)
    txn_df = _filter_intra_import_duplicates(txn_df)
    txn_df = _filter_db_duplicates(txn_df)

    with db.get_connection() as conn:
        existing_columns = db.get_columns(conn, Table.TXNS.value)

    new_columns = [col for col in txn_df.columns if col not in existing_columns]
    final_columns = existing_columns + new_columns
    _add_missing_columns(new_columns)
    import_logger.debug("Final ordered columns: %s", final_columns)

    # Add any missing columns to the DataFrame
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
            except sqlite3.OperationalError:  # pragma: no cover
                logger.exception("Could not add column '%s'", c)


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
        import_logger.debug("Excel->Internal mappings:\n%s", pretty_mapping)
    else:
        import_logger.debug("Excel->Internal mappings: {}")  # pragma: no cover

    # Ensure TXN_ESSENTIALS are present in the mapping
    if unmatched:
        error_message = f"Could not map essential columns: {unmatched}"
        import_logger.error(error_message)
        raise ValueError(error_message)

    excel_df = excel_df.rename(columns=mapping)
    summaries = excel_df.apply(_format_transaction_summary, axis=1)
    for summary in summaries:
        import_logger.info(" - %s", summary)
    return excel_df


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
    return hashlib.sha256(key_string.encode("utf-8")).hexdigest()


def _get_existing_transaction_keys() -> set[str]:
    """Get synthetic keys for all existing transactions in the database.

    Args:
        conn: Database connection.

    Returns:
        Set of synthetic keys for existing transactions.
    """
    with db.get_connection() as conn:
        try:
            # Build the query to select essential columns.
            essential_cols = ", ".join(f'"{col}"' for col in TXN_ESSENTIALS)
            query = f'SELECT {essential_cols} FROM "{Table.TXNS.value}"'  # noqa: S608
            existing_df = pd.read_sql_query(query, conn)
            if existing_df.empty:  # pragma: no cover
                return set()

            existing_keys: set[str] = set()
            existing_keys.update(existing_df.apply(_generate_synthetic_key, axis=1))
        except (sqlite3.Error, pd.errors.DatabaseError):  # pragma: no cover
            import_logger.debug(
                "Table '%s' does not exist yet, no existing transactions to check.",
                Table.TXNS.value,
            )
            return set()
        else:
            return existing_keys


def _filter_db_duplicates(txn_df: pd.DataFrame) -> pd.DataFrame:
    """Filter out transactions that already exist in the database.

    Args:
        txn_df: DataFrame with transaction data.

    Returns:
        DataFrame with database duplicates removed.
    """
    if txn_df.empty:  # pragma: no cover
        return txn_df

    existing_keys = _get_existing_transaction_keys()
    if not existing_keys:  # pragma: no cover
        return txn_df

    new_keys_series: pd.Series[str] = txn_df.apply(_generate_synthetic_key, axis=1)
    new_keys: set[str] = set(new_keys_series)
    duplicates: set[str] = existing_keys & new_keys
    if not duplicates:  # pragma: no cover
        return txn_df

    import_logger.info("Filtered %d database duplicate transactions.", len(duplicates))

    is_duplicate: pd.Series[bool] = new_keys_series.isin(duplicates)
    duplicates_df: pd.DataFrame = txn_df[is_duplicate]
    summaries = duplicates_df.apply(_format_transaction_summary, axis=1)
    for summary in summaries:
        import_logger.info(" - %s", summary)

    return txn_df[~is_duplicate].copy()


def _filter_intra_import_duplicates(txn_df: pd.DataFrame) -> pd.DataFrame:
    """Filter out duplicate transactions within the DataFrame itself.

    Args:
        txn_df: DataFrame with transaction data.

    Returns:
        DataFrame with duplicates removed
    """
    if txn_df.empty:  # pragma: no cover
        return txn_df

    keys = txn_df.apply(_generate_synthetic_key, axis=1)
    duplicate_mask = keys.duplicated(keep="first")
    num_dupes = duplicate_mask.sum()

    if num_dupes > 0:
        duplicate_transactions = txn_df[duplicate_mask]
        import_logger.info(
            "Filtered %d intra-import duplicate transactions.",
            num_dupes,
        )
        summaries = duplicate_transactions.apply(_format_transaction_summary, axis=1)
        for summary in summaries:
            import_logger.info(" - %s", summary)

    return txn_df[~duplicate_mask].copy()


def _format_transaction_summary(row: pd.Series) -> str:
    """Format a transaction row into a human-readable summary.

    Args:
        row: A pandas Series containing transaction data.

    Returns:
        A formatted string summarizing the transaction.
    """
    essential_parts = []
    for col in TXN_ESSENTIALS:
        value = row.get(col, "N/A")
        if pd.isna(value):  # pragma: no cover
            value = "N/A"
        essential_parts.append(f"{col}={value}")

    return "|".join(essential_parts)
