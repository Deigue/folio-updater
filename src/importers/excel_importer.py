"""Ingest Excel data into the application context."""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import TYPE_CHECKING

import pandas as pd

from app.app_context import get_config
from db import db, preparers
from db.utils import format_transaction_summary
from utils.backup import rolling_backup
from utils.constants import Column, Table
from utils.logging_setup import get_import_logger
from utils.settlement_calculator import BUSINESS_DAY_SETTLE_ACTIONS
from utils.transforms import normalize_canadian_ticker

if TYPE_CHECKING:
    from pathlib import Path

    from models.import_results import ImportResults

logger = logging.getLogger(__name__)
import_logger = get_import_logger()


def import_transactions(
    folio_path: Path,
    account: str | None = None,
    sheet: str | None = None,
    *,
    with_results: bool = False,
) -> int | ImportResults:
    """Import transactions from Excel files and map headers to internal fields.

    Keeps TXN_ESSENTIALS first, then existing DB columns, then net new columns.

    Args:
        folio_path (Path): Path to the Excel file containing transactions.
        account (str | None): Optional account identifier to use as fallback
            when Account column is missing from the Excel file.
        sheet (str | None): Optional sheet name to read from the Excel file.
            If None, uses the first sheet in the Excel file.
        with_results (bool): If True, return ImportResults instead of count.

    Returns:
        Depending on with_results, returns ImportResults or count of imported
        transactions.
    """
    is_csv: bool = folio_path.suffix.lower() == ".csv"

    with db.get_connection() as conn:
        existing_count = db.get_row_count(conn, Table.TXNS)

    # Log import start with detailed info
    import_logger.info('IMPORT TXNS "%s"', folio_path)
    import_logger.info("EXISTING %d transactions in database", existing_count)

    try:
        if is_csv:  # pragma: no cover
            txns_df: pd.DataFrame = pd.read_csv(folio_path)
        else:
            if sheet is None:
                with pd.ExcelFile(folio_path, engine="openpyxl") as xls:
                    sheet = str(xls.sheet_names[0])
            txns_df: pd.DataFrame = pd.read_excel(
                folio_path,
                sheet_name=sheet,
            )

        import_logger.info(
            "READ %d transactions from sheet '%s'",
            len(txns_df),
            sheet,
        )
    except ValueError:
        error_msg = f"No '{sheet}' sheet found in {folio_path}."
        import_logger.warning(error_msg)
        import_logger.info("DONE: 0 imported")
        import_logger.info("=" * 80)
        import_logger.info("")
        return 0

    db_path = get_config().db_path
    if existing_count > 0:
        rolling_backup(db_path)

    import_results = preparers.prepare_transactions(txns_df, account)
    prepared_df = import_results.final_df

    with db.get_connection() as conn:
        try:
            prepared_df.to_sql(Table.TXNS, conn, if_exists="append", index=False)
            final_count = db.get_row_count(conn, Table.TXNS)
        except sqlite3.IntegrityError:
            _analyze_and_insert_rows(conn, prepared_df)

    txn_count = len(prepared_df)
    msg: str = f"DONE: {txn_count} imported"
    import_logger.info(msg)
    import_logger.info("TOTAL %d transactions in database", final_count)
    import_logger.info("=" * 80)
    import_logger.info("")
    import_results.existing_count = existing_count
    import_results.final_db_count = final_count
    return import_results if with_results else txn_count


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
                Table.TXNS,
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


def import_statements(statement: Path) -> int:
    """Import monthly statements from Excel files to update settlement dates.

    This function processes statement data to find matching transactions in the
    database and updates their settlement dates with actual values from the statement.

    Expected statement columns:
    - date: Settlement date from the statement
    - amount: Transaction amount (should match existing transaction)
    - currency: Transaction currency
    - transaction: Action type (BUY/SELL/etc.)
    - description: Contains ticker and units info for BUY/SELL transactions
                   Also contains transaction date for matching

    Args:
        statement (Path): Path to the statement file (Excel or CSV).

    Returns:
        int: Number of transactions updated.
    """
    import_logger.info('IMPORT STATEMENT "%s"', statement)

    try:
        if statement.suffix.lower() == ".csv":  # pragma: no cover
            stmt_df = pd.read_csv(statement)
        else:
            stmt_df = pd.read_excel(statement, engine="openpyxl")

        if stmt_df.empty:
            import_logger.warning("EMPTY Statement file")
            return 0

        import_logger.info("READ %d rows from statement", len(stmt_df))
        stmt_df.columns = stmt_df.columns.str.lower().str.strip()
        required_cols = ["date", "amount", "currency", "transaction", "description"]
        missing_cols = [col for col in required_cols if col not in stmt_df.columns]
        if missing_cols:
            import_logger.warning("MISSING COLUMNS: %s", missing_cols)
            return 0

        updates = _update_settlement_dates(stmt_df)
    except (OSError, ValueError, KeyError):
        import_logger.exception("Error processing statement")
        return 0
    else:
        import_logger.info("DONE: Updated %d settlement dates", updates)
        import_logger.info("=" * 80)
        import_logger.info("")
        return updates


def _update_settlement_dates(df: pd.DataFrame) -> int:
    """Update settlement dates using the provided DataFrame."""
    with db.get_connection() as conn:
        condition = f'"{Column.Txn.SETTLE_CALCULATED}" = 1'
        existing_txns = db.get_rows(
            conn,
            Table.TXNS,
            condition=condition,
            order_by=f'"{Column.Txn.TXN_DATE}", "{Column.Txn.TXN_ID}"',
        )

        if existing_txns.empty:  # pragma: no cover
            import_logger.info("No calculated settlement dates found to update")
            return 0

        updates = []
        candidate_count = 0
        for _, row in df.iterrows():
            try:
                statement_data = _extract_statement_row_data(row)
                if not statement_data:  # pragma: no cover
                    continue
                candidate_count += 1
                matches = _match_transactions(existing_txns, statement_data)

                if len(matches) == 1:
                    transaction_row = matches.iloc[0]
                    txn_id = int(transaction_row[Column.Txn.TXN_ID])
                    updates.append(
                        {
                            Column.Txn.TXN_ID: txn_id,
                            Column.Txn.SETTLE_DATE: statement_data["settlement_date"],
                            Column.Txn.SETTLE_CALCULATED: 0,
                        },
                    )
                    txn_summary = format_transaction_summary(transaction_row)
                    import_logger.info("  * %s", txn_summary)
                elif len(matches) > 1:  # pragma: no cover
                    import_logger.warning(
                        "Multiple matches found for %s %s on %s, skipping",
                        statement_data["action"],
                        statement_data["ticker"],
                        statement_data["txn_date"],
                    )

            except (ValueError, TypeError) as e:
                import_logger.warning("Skipping invalid statement row: %s", e)
                continue

        if candidate_count != 0:
            import_logger.info(
                "FOUND %d MATCHES OUT OF %d CANDIDATES",
                len(updates),
                candidate_count,
            )
        if updates:
            return _apply_settlement_updates_to_db(conn, updates)

        return 0  # pragma: no cover


def _extract_statement_row_data(row: pd.Series) -> dict | None:
    """Extract and validate data from a statement row."""
    settlement_date = _normalize_date(row["date"])
    if not settlement_date:  # pragma: no cover
        return None

    action_str = str(row["transaction"]).strip().upper()
    if action_str not in BUSINESS_DAY_SETTLE_ACTIONS:  # pragma: no cover
        return None

    description = str(row["description"])
    ticker, units, txn_date = _parse_transaction_description(description)

    if not ticker or not txn_date:  # pragma: no cover
        return None

    currency = row["currency"]
    ticker = normalize_canadian_ticker(ticker, currency)

    return {
        "settlement_date": settlement_date,
        "action": action_str,
        "ticker": ticker,
        "units": units,
        "txn_date": txn_date,
        "currency": currency,
        "amount": abs(float(row["amount"])),
    }


def _normalize_date(date_value: str) -> str | None:
    """Normalize date to YYYY-MM-DD format."""
    if pd.isna(date_value):  # pragma: no cover
        return None

    date_str = str(date_value).strip()
    try:
        parsed_date = pd.to_datetime(date_str)
        return parsed_date.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _parse_transaction_description(
    description: str,
) -> tuple[str | None, float | None, str | None]:
    """Extract ticker, units, and transaction date from description.

    Args:
        description: Statement description text

    Returns:
        Tuple of (ticker, units, transaction_date)
    """
    description = description.upper()

    # Extract ticker at the start of the description (before ' - ')
    ticker_match = re.match(r"^([A-Z]{1,5}(?:[.-][A-Z]{1,5})?)\s+-", description)
    ticker = ticker_match.group(1) if ticker_match else None

    units = None
    # Look for patterns like "100 SHARES", "50.5 UNITS", etc.
    units_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:SHARES?|UNITS?)", description)
    if units_match:
        units = float(units_match.group(1))

    txn_date = None
    date_patterns = [
        r"(\d{4}-\d{2}-\d{2})",  # YYYY-MM-DD
        r"(\d{2}/\d{2}/\d{4})",  # MM/DD/YYYY or DD/MM/YYYY
        r"(\d{2}-\d{2}-\d{4})",  # MM-DD-YYYY or DD-MM-YYYY
    ]

    for pattern in date_patterns:
        date_match = re.search(pattern, description)
        if date_match:
            txn_date = _normalize_date(date_match.group(1))
            break

    return ticker, units, txn_date


def _match_transactions(
    existing_txns: pd.DataFrame,
    statement_data: dict,
) -> pd.DataFrame:
    """Find transactions matching the statement data."""
    conditions = (
        (existing_txns[Column.Txn.ACTION] == statement_data["action"])
        & (existing_txns[Column.Txn.TXN_DATE] == statement_data["txn_date"])
        & (existing_txns[Column.Txn.TICKER] == statement_data["ticker"])
        & (existing_txns[Column.Txn.CURRENCY] == statement_data["currency"])
    )

    # Matching tolerance for numeric amounts
    amount_tolerance = 0.01
    stmt_amount = statement_data["amount"]

    def amount_matches(x: float) -> bool:
        return abs(abs(float(x)) - stmt_amount) < amount_tolerance

    amount_conditions = existing_txns[Column.Txn.AMOUNT].apply(amount_matches)
    conditions &= amount_conditions

    # Units matching if available
    if statement_data["units"] and statement_data["units"] > 0:
        units_tolerance = 0.0001
        stmt_units = statement_data["units"]

        def units_matches(x: float) -> bool:
            return abs(abs(float(x)) - stmt_units) < units_tolerance

        units_conditions = existing_txns[Column.Txn.UNITS].apply(units_matches)
        conditions &= units_conditions

    return existing_txns[conditions]


def _apply_settlement_updates_to_db(
    conn: sqlite3.Connection,
    updates: list[dict],
) -> int:
    """Apply settlement date updates in batch and log audit trail."""
    if not updates:  # pragma: no cover
        return 0

    rolling_backup(get_config().db_path)
    return db.update_rows(
        conn,
        Table.TXNS,
        updates,
        where_columns=[Column.Txn.TXN_ID],
        set_columns=[Column.Txn.SETTLE_DATE, Column.Txn.SETTLE_CALCULATED],
    )
