"""Ingest Excel data into the application context."""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import TYPE_CHECKING

import pandas as pd

from db import (
    backup_folio,
    get_connection,
    get_row_count,
    get_rows,
    prepare_transactions,
    txn_count,
    update_rows,
)
from db.helpers import format_transaction_summary
from models import ImportResults, StatementImportResult
from ui import get_symbol
from utils import (
    TXN_ESSENTIALS,
    Action,
    Column,
    Table,
    audit_footer,
    get_import_logger,
    info_both,
    warning_both,
)
from utils.settlement_calculator import BUSINESS_DAY_SETTLE_ACTIONS
from utils.transforms import normalize_canadian_ticker

if TYPE_CHECKING:
    from pathlib import Path

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

    existing_count = txn_count()

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
        audit_footer()
        return 0

    backup_folio()

    import_results = prepare_transactions(txns_df, account)
    prepared_df = import_results.final_df

    with get_connection() as conn:
        try:
            prepared_df.to_sql(Table.TXNS, conn, if_exists="append", index=False)
        except sqlite3.IntegrityError:
            _analyze_and_insert_rows(conn, prepared_df)
        final_count = get_row_count(conn, Table.TXNS)

    imported_count = len(prepared_df)
    msg: str = f"DONE: {imported_count} imported"
    import_logger.info(msg)
    summaries = import_results.final_df.apply(format_transaction_summary, axis=1)
    for summary in summaries:
        import_logger.info(" + %s", summary)
    import_logger.info("TOTAL %d transactions in database", final_count)
    audit_footer()
    import_results.existing_count = existing_count
    import_results.final_db_count = final_count
    return import_results if with_results else imported_count


def _analyze_and_insert_rows(
    conn: sqlite3.Connection,
    prepared_df: pd.DataFrame,
) -> None:  # pragma: no cover
    """Analyze and insert rows one by one to identify problematic transactions.

    Args:
        conn: Database connection
        prepared_df: DataFrame with prepared transaction data

    Returns:
        Final count of transactions in database
    """
    info = get_symbol("info")
    success = get_symbol("success")
    error = get_symbol("error")
    analysis_header = f"{info}BULK INSERT FAILED - Analyzing transactions..."
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
            success_msg = f"{success}Row {idx}/{total_rows}: SUCCESS"
            import_logger.info(success_msg)

    except sqlite3.IntegrityError as row_error:
        transaction_summary = format_transaction_summary(row)
        error_msg = f"{error}Row {idx}/{total_rows}: FAILED - {row_error}"
        transaction_msg = f"   {transaction_summary}"
        import_logger.info(error_msg)
        import_logger.info(transaction_msg)
        raise


def import_statements(statement: Path) -> StatementImportResult:
    """Import monthly statement data: settle dates and institutional transfers.

    A statement serves two purposes. For trades already in the folio, finds
    matching transactions and backfills its settlement date. It is also the only source
    with a real amount and date for institutional transfers, so TFR_IN/TFR_OUT
    transactions are created here.

    Expected statement columns:
    - date: Settlement date from the statement
    - amount: Transaction amount (should match existing transaction)
    - currency: Transaction currency
    - transaction: Action types (BUY/SELL/TRFOUT/TRFIN etc.)
    - description: Contains ticker and units info for BUY/SELL transactions
                   Also contains transaction date for matching

    Args:
        statement (Path): Path to the statement file (Excel or CSV).

    Returns:
        StatementImportResult: Settlement dates updated and transfers created.
    """
    import_logger.info('IMPORT STATEMENT "%s"', statement)

    try:
        if statement.suffix.lower() == ".csv":  # pragma: no cover
            stmt_df = pd.read_csv(statement)
        else:
            stmt_df = pd.read_excel(statement, engine="openpyxl")

        if stmt_df.empty:
            warning_both("Statement file is empty.", "importer")
            return StatementImportResult()

        import_logger.info("READ %d rows from statement", len(stmt_df))
        stmt_df.columns = stmt_df.columns.str.lower().str.strip()
        required_cols = ["date", "amount", "currency", "transaction", "description"]
        missing_cols = [col for col in required_cols if col not in stmt_df.columns]
        if missing_cols:
            warning_both(
                f"Statement is missing required columns: {missing_cols}",
                "importer",
            )
            return StatementImportResult()

        settlement_updates = _update_settlement_dates(stmt_df)
        transfer_df, transfers_rejected, transfers_skipped = (
            _build_transfer_transactions(stmt_df, statement)
        )
        transfer_results = (
            _create_transfer_transactions(transfer_df)
            if not transfer_df.empty
            else None
        )
    except (OSError, ValueError, KeyError):
        import_logger.exception("Error processing statement")
        return StatementImportResult()
    else:
        import_logger.info(
            "DONE: Updated %d settlement date(s), created %d transfer(s), "
            "skipped %d cash transfer(s)",
            settlement_updates,
            transfer_results.imported_count() if transfer_results else 0,
            transfers_skipped,
        )
        audit_footer()
        return StatementImportResult(
            settlement_updates=settlement_updates,
            transfer_results=transfer_results,
            transfers_rejected=transfers_rejected,
            transfers_skipped=transfers_skipped,
        )


def _update_settlement_dates(df: pd.DataFrame) -> int:
    """Update settlement dates using the provided DataFrame."""
    with get_connection() as conn:
        where = f'"{Column.Txn.SETTLE_CALCULATED}" = ?'
        params = [1]
        existing_txns = get_rows(
            conn,
            Table.TXNS,
            where=where,
            params=params,
            order_by=f'"{Column.Txn.TXN_DATE}", "{Column.Txn.TXN_ID}"',
        )

        if existing_txns.empty:  # pragma: no cover
            info_both("No calculated settlement dates found to update", "importer")
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
                    msg = (
                        f"Multiple matches found for {statement_data['action']} "
                        f"{statement_data['ticker']} on {statement_data['txn_date']}, "
                        "skipping"
                    )
                    warning_both(msg, "importer")

            except (ValueError, TypeError) as e:
                warning_both(f"Skipping invalid statement row: {e}", "importer")
                continue

        if candidate_count != 0:
            info_both(
                f"Matched {len(updates)} out of {candidate_count} candidates.",
                "importer",
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

    backup_folio()
    return update_rows(
        conn,
        Table.TXNS,
        updates,
        where_columns=[Column.Txn.TXN_ID],
        set_columns=[Column.Txn.SETTLE_DATE, Column.Txn.SETTLE_CALCULATED],
    )


_TRANSFER_OUT_PREFIX = "TRFOUT"
_TRANSFER_IN_PREFIX = "TRFIN"
_CASH_TRANSFER_DESCRIPTION_PREFIX = "money transfer"
# Wealthsimple statement files "ws_statement_{account}_{yyyymm}.{ext}"
_STATEMENT_FILENAME_PATTERN = re.compile(
    r"^ws_statement_(?P<account>.+)_\d{6}$",
    re.IGNORECASE,
)


def _transfer_action_for_code(transaction_type: str) -> str | None:
    """Map a statement transfer code to a TFR action, or None if it isn't one."""
    code = transaction_type.strip().upper()
    if code.startswith(_TRANSFER_OUT_PREFIX):
        return Action.TFR_OUT
    if code.startswith(_TRANSFER_IN_PREFIX):
        return Action.TFR_IN
    return None


def _is_cash_transfer(description: str) -> bool:
    """Report whether a transfer row is a bank cash transfer."""
    return description.strip().lower().startswith(_CASH_TRANSFER_DESCRIPTION_PREFIX)


def _extract_account_from_statement_filename(path: Path) -> str | None:
    """Recover the account alias from a Wealthsimple statement filename."""
    match = _STATEMENT_FILENAME_PATTERN.match(path.stem)
    return match.group("account") if match else None


def _build_transfer_transactions(
    df: pd.DataFrame,
    statement_path: Path,
) -> tuple[pd.DataFrame, int, int]:
    """Build TFR_IN/TFR_OUT transactions from transfer rows in a statement.

    Returns:
        Candidate transactions (possibly empty), a count of transfer rows found
        that could not be turned into a transaction, and a count of cash
        transfers skipped because the activities import already covers them.
    """
    account = _extract_account_from_statement_filename(statement_path)
    rows: list[dict[str, object]] = []
    rejected = 0
    skipped = 0

    for _, row in df.iterrows():
        action = _transfer_action_for_code(str(row["transaction"]))
        if action is None:
            continue

        if _is_cash_transfer(str(row["description"])):
            skipped += 1
            import_logger.info(
                " - Skipping cash transfer (imported as a contribution or "
                "withdrawal): %s, %s, %s",
                row["transaction"],
                row["date"],
                row["amount"],
            )
            continue

        settle_date = _normalize_date(row["date"])
        if not account or not settle_date:
            rejected += 1
            reason = "no account in filename" if not account else "unparseable date"
            warning_both(
                f'Skipping transfer in "{statement_path.name}": {reason} '
                f"({row['transaction']}, {row['date']})",
                "importer",
            )
            continue

        # Build baseline transaction layout with essential columns.
        txn: dict[str, object] = dict.fromkeys(TXN_ESSENTIALS, pd.NA)
        txn.update(
            {
                Column.Txn.TXN_DATE: settle_date,
                Column.Txn.ACTION: action,
                Column.Txn.AMOUNT: row["amount"],
                Column.Txn.CURRENCY: row["currency"],
                Column.Txn.ACCOUNT: account,
            },
        )
        rows.append(txn)

    return pd.DataFrame(rows), rejected, skipped


def _create_transfer_transactions(transfer_df: pd.DataFrame) -> ImportResults:
    """Pass transfer transactions to the standard import pipeline."""
    import_results = prepare_transactions(transfer_df, map_headers=False)
    prepared_df = import_results.final_df
    existing_count = txn_count()

    if not prepared_df.empty:
        backup_folio()
        with get_connection() as conn:
            try:
                prepared_df.to_sql(Table.TXNS, conn, if_exists="append", index=False)
            except sqlite3.IntegrityError:
                _analyze_and_insert_rows(conn, prepared_df)

    import_logger.info(
        "IMPORT %d transfer transaction(s) from statement",
        len(prepared_df),
    )
    for summary in prepared_df.apply(format_transaction_summary, axis=1):
        import_logger.info(" + %s", summary)

    import_results.existing_count = existing_count
    import_results.final_db_count = txn_count()
    return import_results
