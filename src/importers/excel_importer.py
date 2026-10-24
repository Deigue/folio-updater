"""Ingest Excel data into the application context."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from app.logging_setup import audit_footer, get_import_logger
from db import (
    append_rows,
    backup_folio,
    get_connection,
    get_rows,
    trial_insert_rows,
    txn_count,
    update_rows,
)
from db.helpers import format_transaction_summary
from domain import TXN_ESSENTIALS, Action, Column, SettlementOutcome, Table
from engine.settlement import BUSINESS_DAY_SETTLE_ACTIONS
from ingest import TransactionTransformer, prepare_transactions
from models import ImportResults, SettlementMatch, StatementImportResult
from services.symbols import normalize_canadian_ticker
from term import announce, get_symbol

if TYPE_CHECKING:
    from pathlib import Path

import_logger = get_import_logger()


def import_transactions(
    folio_path: Path,
    account: str | None = None,
    sheet: str | None = None,
) -> ImportResults:
    """Read a source file and add the transactions that survive ingest to the folio.

    Args:
        folio_path (Path): Path to the Excel or CSV file containing transactions.
        account (str | None): Optional account identifier to use as fallback
            when Account column is missing from the file.
        sheet (str | None): Optional sheet name to read from an Excel file.
            If None, uses the first sheet in the file.

    Returns:
        Every stage of the preparation, with the database counts before and
        after filled in.

    Raises:
        ValueError: If the file cannot be read, such as an Excel file with no
            sheet of that name.
    """
    existing_count = txn_count()

    # Log import start with detailed info
    import_logger.info('IMPORT TXNS "%s"', folio_path)
    import_logger.info("EXISTING %d transactions in database", existing_count)

    try:
        try:
            txns_df = _read_source(folio_path, sheet)
        except ValueError as error:
            import_logger.warning('CANNOT READ "%s": %s', folio_path, error)
            import_logger.info("DONE: 0 imported")
            raise

        where = f" from sheet '{sheet}'" if sheet else ""
        import_logger.info("READ %d transactions%s", len(txns_df), where)

        import_results = insert_transactions(txns_df, account)

        import_logger.info("DONE: %d imported", import_results.imported_count())
        _log_imported(import_results.final_df)
        import_logger.info(
            "TOTAL %d transactions in database",
            import_results.final_db_count,
        )
    finally:
        audit_footer()
    return import_results


def _read_source(source: Path, sheet: str | None = None) -> pd.DataFrame:
    """Read a CSV, or one sheet of an Excel file, into a frame.

    Args:
        source: The file to read.
        sheet: The sheet to read, or None for the first. A CSV has no sheets.

    Returns:
        The rows, as the file holds them.
    """
    if source.suffix.lower() == ".csv":  # pragma: no cover
        return pd.read_csv(source)
    return pd.read_excel(source, sheet_name=0 if sheet is None else sheet)


def _log_imported(imported: pd.DataFrame) -> None:
    """Add a line to the audit log for each transaction that was imported."""
    for summary in imported.apply(format_transaction_summary, axis=1):
        import_logger.info(" + %s", summary)


def insert_transactions(
    txns_df: pd.DataFrame,
    account: str | None = None,
    *,
    map_headers: bool = True,
) -> ImportResults:
    """Run rows through the ingest pipeline and store the ones that survive it.

    The folio is backed up first whenever anything is about to be written.

    Args:
        txns_df: The rows to add, as read from a source or built in code.
        account: Fallback account for rows that name none.
        map_headers: Whether the columns still carry a source's own headers.
            False when they are already keyed by internal names.

    Returns:
        Every stage of the preparation, with the folio's count after the
        import filled in.

    Raises:
        sqlite3.IntegrityError: If the table refuses a row that ingest passed.
            The audit log names the row, and none of the batch is stored.
    """
    import_results = prepare_transactions(txns_df, account, map_headers=map_headers)
    prepared_df = import_results.final_df

    if not prepared_df.empty:
        backup_folio()
        with get_connection() as conn:
            try:
                append_rows(conn, Table.TXNS, prepared_df)
            except sqlite3.IntegrityError:
                _log_refused_row(conn, prepared_df)
                raise

    import_results.final_db_count = txn_count()
    return import_results


def _log_refused_row(conn: sqlite3.Connection, prepared_df: pd.DataFrame) -> None:
    """Name, in the audit log, the transaction of a refused batch that breaks the table.

    The rows are tried one at a time in a transaction that is rolled back, so
    the first row the table refuses is found exactly, a row can still clash
    with one before it in the batch, and nothing is left stored.

    Args:
        conn: Database connection.
        prepared_df: The rows the table refused as a batch.
    """
    info = get_symbol("info")
    success = get_symbol("success")
    error = get_symbol("error")
    import_logger.error("%sBULK INSERT FAILED - Analyzing transactions...", info)

    total_rows = len(prepared_df)
    try:
        outcomes = trial_insert_rows(conn, Table.TXNS, prepared_df)
    except sqlite3.Error:
        # The batch's own error is what the caller raises; this is only a guide.
        import_logger.exception("Could not try the rows one at a time")
        return
    for position, refusal in enumerate(outcomes, 1):
        if refusal is None:
            import_logger.info("%sRow %d/%d: SUCCESS", success, position, total_rows)
            continue
        import_logger.info(
            "%sRow %d/%d: FAILED - %s",
            error,
            position,
            total_rows,
            refusal,
        )
        summary = format_transaction_summary(prepared_df.iloc[position - 1])
        import_logger.info("   %s", summary)


def import_statements(statement: Path) -> StatementImportResult:
    """Import monthly statement data: settlement dates and institutional transfers.

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
                   Also contains trade date for matching

    Args:
        statement (Path): Path to the statement file (Excel or CSV).

    Returns:
        StatementImportResult: Settlement dates updated and transfers created.
    """
    import_logger.info('IMPORT STATEMENT "%s"', statement)
    try:
        return _import_statement_rows(statement)
    finally:
        audit_footer()


def _import_statement_rows(statement: Path) -> StatementImportResult:
    """Read a statement and apply it, whether or not the file turns out usable."""
    try:
        stmt_df = _read_source(statement)

        if stmt_df.empty:
            announce.warning("Statement file is empty.", "importer")
            return StatementImportResult()

        import_logger.info("READ %d rows from statement", len(stmt_df))
        stmt_df.columns = stmt_df.columns.str.lower().str.strip()
        required_cols = ["date", "amount", "currency", "transaction", "description"]
        missing_cols = [col for col in required_cols if col not in stmt_df.columns]
        if missing_cols:
            announce.warning(
                f"Statement is missing required columns: {missing_cols}",
                "importer",
            )
            return StatementImportResult()

        settlement_updates, settlement_matches = _update_settlement_dates(
            stmt_df,
            statement,
        )
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
        return StatementImportResult(
            settlement_updates=settlement_updates,
            transfer_results=transfer_results,
            transfers_rejected=transfers_rejected,
            transfers_skipped=transfers_skipped,
            settlement_matches=settlement_matches,
        )


def _update_settlement_dates(
    df: pd.DataFrame,
    statement: Path,
) -> tuple[int, list[SettlementMatch]]:
    """Update settlement dates using the provided DataFrame.

    Args:
        df: Rows read from the statement.
        statement: Path the rows came from. Its filename names the account,
            which keeps two accounts holding the same trade apart.

    Returns:
        The number of transactions updated, and one `SettlementMatch` per
        candidate row describing how it was resolved.
    """
    account = _extract_account_from_statement_filename(statement)
    if not account:
        announce.warning(
            f'No account in statement filename "{statement.name}"; settlement '
            "matching cannot tell two accounts' identical trades apart",
            "importer",
        )

    with get_connection() as conn:
        all_txns = get_rows(
            conn,
            Table.TXNS,
            order_by=f'"{Column.Txn.TXN_DATE}", "{Column.Txn.TXN_ID}"',
        )
        calculated = all_txns[Column.Txn.SETTLE_CALCULATED] == 1
        existing_txns = all_txns[calculated]
        settled_txns = all_txns[~calculated]

        if all_txns.empty:  # pragma: no cover
            announce.info("No transactions found to settle", "importer")
            return 0, []

        updates: list[dict] = []
        results: list[SettlementMatch] = []
        for _, row in df.iterrows():
            try:
                statement_row = _extract_statement_row_data(row, account)
                if statement_row is None:
                    continue
                matches = _match_transactions(existing_txns, statement_row)
                result, update = _resolve_statement_match(
                    statement_row,
                    matches,
                    settled_txns,
                )
                results.append(result)
                if update is not None:
                    updates.append(update)
            except (ValueError, TypeError) as e:
                announce.warning(f"Skipping invalid statement row: {e}", "importer")
                continue

        if results:
            announce.info(
                f"Matched {len(updates)} out of {len(results)} candidates.",
                "importer",
            )
        if updates:
            return _apply_settlement_updates_to_db(conn, updates), results

        return 0, results


def _resolve_statement_match(
    statement_row: _StatementRow,
    matches: pd.DataFrame,
    settled_txns: pd.DataFrame,
) -> tuple[SettlementMatch, dict | None]:
    """Turn a row's candidate matches into an outcome, with an update for a hit.

    Args:
        statement_row: Fields extracted from the statement row.
        matches: Transactions with a calculated date that the row matched.
        settled_txns: Transactions whose settlement date is already set properly.

    Returns:
        The outcome recorded for this statement row, and the update to apply
        when exactly one transaction matched (None otherwise).
    """
    result = SettlementMatch(
        outcome=SettlementOutcome.UNMATCHED,
        settle_date=statement_row.settlement_date,
        txn_date=statement_row.txn_date,
        action=statement_row.action,
        ticker=statement_row.ticker,
        currency=statement_row.currency,
        amount=statement_row.amount,
        units=statement_row.units,
        account=statement_row.account,
        candidates=len(matches),
    )

    if len(matches) == 1:
        transaction_row = matches.iloc[0]
        result.txn_id = int(transaction_row[Column.Txn.TXN_ID])
        result.outcome = SettlementOutcome.MATCHED
        update = {
            Column.Txn.TXN_ID: result.txn_id,
            Column.Txn.SETTLE_DATE: statement_row.settlement_date,
            Column.Txn.SETTLE_CALCULATED: 0,
        }
        import_logger.info("  * %s", format_transaction_summary(transaction_row))
        return result, update

    described = (
        f"{result.action} {result.ticker} on {result.txn_date} for {result.amount}"
    )
    if len(matches) > 1:
        result.outcome = SettlementOutcome.AMBIGUOUS
        import_logger.warning(
            "  ? %d matches for %s, skipping",
            len(matches),
            described,
        )
        return result, None

    already = _match_transactions(settled_txns, statement_row)
    if len(already) == 1:
        result.outcome = SettlementOutcome.ALREADY_SETTLED
        result.txn_id = int(already.iloc[0][Column.Txn.TXN_ID])
        result.candidates = 1
        import_logger.info("  = Already settled: %s", described)
    else:
        import_logger.warning("  ! No match for %s", described)
    return result, None


@dataclass(frozen=True)
class _StatementRow:
    """The fields of a statement row that settlement matching runs on.

    Attributes:
        settlement_date: Settlement date from the statement, as YYYY-MM-DD.
        action: Statement action code, such as BUY or SELL.
        ticker: Ticker spelled the way the folio stores it.
        units: Units named in the description, when it names any.
        txn_date: Trade date named in the description, as YYYY-MM-DD.
        currency: Currency the row is denominated in.
        account: Account the statement belongs to, or None when unknown.
        amount: The amount, without its sign.
    """

    settlement_date: str
    action: str
    ticker: str
    units: float | None
    txn_date: str
    currency: str
    account: str | None
    amount: float


def _extract_statement_row_data(
    row: pd.Series,
    account: str | None,
) -> _StatementRow | None:
    """Extract and validate data from a statement row.

    Args:
        row: One statement row.
        account: Account the statement belongs to, or None when unknown.

    Returns:
        Fields to match on, or None when the row is not a settling trade.
    """
    settlement_date = _normalize_date(row["date"])
    if not settlement_date:  # pragma: no cover
        return None

    action_str = str(row["transaction"]).strip().upper()
    if action_str not in BUSINESS_DAY_SETTLE_ACTIONS:
        return None

    description = str(row["description"])
    ticker, units, txn_date = _parse_transaction_description(description)

    if not ticker or not txn_date:  # pragma: no cover
        return None

    currency = row["currency"]
    ticker = _canonical_statement_ticker(ticker, action_str, currency)

    return _StatementRow(
        settlement_date=settlement_date,
        action=action_str,
        ticker=ticker,
        units=units,
        txn_date=txn_date,
        currency=str(currency),
        account=account,
        amount=abs(float(row["amount"])),
    )


def _canonical_statement_ticker(
    ticker: str,
    action: str,
    currency: object,
) -> str:
    """Spell a statement's ticker the way the folio stores it.

    Two steps, in the order the import pipeline runs them: add the exchange
    suffix a CAD ticker is missing, then apply the user's transform rules.
    Skipping the second step is what left venue-corrected symbols unmatchable.

    Args:
        ticker: Ticker as written in the statement description.
        action: Statement action code, in case a rule keys on it.
        currency: Currency the row is denominated in.

    Returns:
        The transformed ticker, or the normalized one if no rule applied.
    """
    normalized = normalize_canadian_ticker(ticker, str(currency)) or ticker
    transformed = TransactionTransformer.transform_fields(
        {
            Column.Txn.TICKER: normalized,
            Column.Txn.ACTION: action,
            Column.Txn.CURRENCY: currency,
        },
    )
    return str(transformed.get(Column.Txn.TICKER, normalized))


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
    """Extract ticker, units, and trade date from description.

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
    statement_row: _StatementRow,
) -> pd.DataFrame:
    """Find transactions matching the statement data.

    Args:
        existing_txns: Transactions to look through, such as those still
            carrying a calculated settlement date.
        statement_row: Fields extracted from one statement row.

    Returns:
        Every transaction the row matches.
    """
    conditions = (
        (existing_txns[Column.Txn.ACTION] == statement_row.action)
        & (existing_txns[Column.Txn.TXN_DATE] == statement_row.txn_date)
        & (existing_txns[Column.Txn.TICKER] == statement_row.ticker)
        & (existing_txns[Column.Txn.CURRENCY] == statement_row.currency)
    )

    if statement_row.account:
        conditions &= existing_txns[Column.Txn.ACCOUNT] == statement_row.account

    # Matching tolerance for numeric amounts
    amount_tolerance = 0.01
    stmt_amount = statement_row.amount

    def amount_matches(x: float) -> bool:
        return abs(abs(float(x)) - stmt_amount) < amount_tolerance

    amount_conditions = existing_txns[Column.Txn.AMOUNT].apply(amount_matches)
    conditions &= amount_conditions

    # Units matching if available
    if statement_row.units and statement_row.units > 0:
        units_tolerance = 0.0001
        stmt_units = statement_row.units

        def units_matches(x: float) -> bool:
            return abs(abs(float(x)) - stmt_units) < units_tolerance

        units_conditions = existing_txns[Column.Txn.UNITS].apply(units_matches)
        conditions &= units_conditions

    return existing_txns[conditions]


def _apply_settlement_updates_to_db(
    conn: sqlite3.Connection,
    updates: list[dict],
) -> int:
    """Back up the folio, then apply settlement date updates in one batch."""
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
            announce.warning(
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
    import_results = insert_transactions(transfer_df, map_headers=False)
    prepared_df = import_results.final_df

    import_logger.info(
        "IMPORT %d transfer transaction(s) from statement",
        len(prepared_df),
    )
    _log_imported(prepared_df)
    return import_results
