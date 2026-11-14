"""Ingest Excel data into the application context."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import pandas as pd

from app import audit
from app.audit import AuditMark, audit_txns, format_transaction_summary
from db import DatabaseError, IntegrityError, current_backup, txns
from domain import TXN_ESSENTIALS, Action, Column, SettlementOutcome
from domain.numeric import CENT, UNIT_EPSILON
from engine.settlement import BUSINESS_DAY_SETTLE_ACTIONS
from ingest import TransactionTransformer, prepare_transactions
from models import ImportResults, SettlementMatch, StatementImportResult
from services.broker_files import statement_account_of
from services.symbols import normalize_canadian_ticker
from term import console_info, console_warning

if TYPE_CHECKING:
    from decimal import Decimal
    from pathlib import Path

    from models import NewTxn, Txn

logger = logging.getLogger(__name__)


def import_transactions(
    file_path: Path,
    account: str | None = None,
    sheet: str | None = None,
) -> ImportResults:
    """Read a source file and add the transactions that survive ingest to the folio.

    Args:
        file_path (Path): Path to the Excel or CSV file containing transactions.
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
    where = f" sheet '{sheet}'" if sheet else ""
    with audit.entry('IMPORT TXNS "%s"%s', file_path, where) as entry:
        txns_df = _read_source(file_path, sheet)
        audit.stage(
            "READ %d source rows; folio holds %d transactions",
            len(txns_df),
            txns.count(),
        )
        import_results = insert_transactions(txns_df, account)
        entry.done(
            "%d stored, %d rejected; folio holds %d transactions",
            import_results.imported_count(),
            import_results.rejected_count(),
            import_results.final_db_count,
        )
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
        IntegrityError: If the table refuses a row that ingest passed. The audit
            log names the row, and none of the batch is stored.
    """
    import_results = prepare_transactions(txns_df, account, map_headers=map_headers)
    approved = import_results.approved

    if approved:
        try:
            txn_ids = txns.append(approved)
        except IntegrityError:
            _log_refused_row(approved)
            raise
        import_results.stored = [
            new.stored_as(txn_id) for new, txn_id in zip(approved, txn_ids, strict=True)
        ]
        _log_stored(import_results.stored)

    import_results.final_db_count = txns.count()
    return import_results


def _log_stored(stored: list[Txn]) -> None:
    """Write the STORE stage: the TxnIds given, the backup that undoes it, each row."""
    audit.stage(
        "STORE %d transaction(s) (TxnIds %s-%s); %s",
        len(stored),
        stored[0].txn_id,
        stored[-1].txn_id,
        audit.describe_backup(current_backup()),
    )
    audit_txns(AuditMark.STORED, stored)


def _log_refused_row(approved: list[NewTxn]) -> None:
    """Name, in the audit log, the transaction of a refused batch that breaks the table.

    The rows are tried one at a time in a transaction that is rolled back, so
    the first row the table refuses is found exactly, a row can still clash
    with one before it in the batch, and nothing is left stored.

    Args:
        approved: The transactions the table refused as a batch.
    """
    audit.stage(
        "TRY %d row(s) one at a time: the table refused the batch",
        len(approved),
        level=logging.ERROR,
    )
    try:
        outcomes = txns.trial_append(approved)
    except DatabaseError:
        # The batch's own error is what the caller raises; this is only a guide.
        logger.exception("Could not try the rows one at a time")
        return
    for txn, refusal in zip(approved, outcomes, strict=False):
        summary = format_transaction_summary(txn.to_row())
        if refusal is None:
            audit.row(AuditMark.STORED, summary)
        else:
            audit.row(AuditMark.REMOVED, f"{summary} ({refusal})", level=logging.ERROR)


def import_statements(statement: Path) -> StatementImportResult:
    """Import monthly statement data: settlement dates and institutional transfers.

    A statement serves two purposes. For trades already in the folio, finds
    matching transactions and confirms its settlement date. It is also the only source
    with a real amount and date for institutional transfers, so TFR_IN/TFR_OUT
    transactions are created here.

    Expected statement columns:
    - date: Settlement date from the statement
    - amount: Transaction amount (should match existing transaction)
    - currency: Transaction currency
    - transaction: Broker actions (BUY/SELL/TRFOUT/TRFIN etc.)
    - description: Contains ticker and units info for BUY/SELL transactions
                   Also contains transaction date for matching

    Args:
        statement (Path): Path to the statement file (Excel or CSV).

    Returns:
        StatementImportResult: Settlement dates confirmed and transfers created.
    """
    with audit.entry('IMPORT STATEMENT "%s"', statement) as entry:
        return _import_statement_rows(statement, entry)


def _import_statement_rows(
    statement: Path,
    entry: audit.AuditEntry,
) -> StatementImportResult:
    """Read a statement and apply it, whether or not the file turns out usable."""
    try:
        stmt_df = _read_source(statement)

        if stmt_df.empty:
            console_warning("Statement file is empty.")
            entry.done("nothing read: the statement is empty")
            return StatementImportResult()

        audit.stage("READ %d statement rows", len(stmt_df))
        stmt_df.columns = stmt_df.columns.str.lower().str.strip()
        required_cols = ["date", "amount", "currency", "transaction", "description"]
        missing_cols = [col for col in required_cols if col not in stmt_df.columns]
        if missing_cols:
            console_warning(f"Statement is missing required columns: {missing_cols}")
            entry.failed(f"missing required columns {missing_cols}")
            return StatementImportResult()

        settlements_confirmed, settlement_matches = _confirm_settlement_dates(
            stmt_df,
            statement,
        )
        transfer_df, rejected, skipped = _build_transfer_transactions(
            stmt_df,
            statement,
        )
        transfer_results = _store_transfers(transfer_df, rejected, skipped)
    except (OSError, ValueError, KeyError) as error:
        logger.exception("Error processing statement %s", statement)
        entry.failed(error)
        return StatementImportResult()
    else:
        entry.done(
            "%d settlement date(s) confirmed, %d transfer(s) stored, "
            "%d cash transfer(s) left to the activities import",
            settlements_confirmed,
            transfer_results.imported_count() if transfer_results else 0,
            len(skipped),
        )
        return StatementImportResult(
            settlements_confirmed=settlements_confirmed,
            transfer_results=transfer_results,
            transfers_rejected=len(rejected),
            transfers_skipped=len(skipped),
            settlement_matches=settlement_matches,
        )


def _confirm_settlement_dates(
    df: pd.DataFrame,
    statement: Path,
) -> tuple[int, list[SettlementMatch]]:
    """Confirm settlement dates using the provided DataFrame.

    Args:
        df: Rows read from the statement.
        statement: Path the rows came from. Its filename names the account,
            which keeps two accounts holding the same trade apart.

    Returns:
        The number of transactions confirmed, and one `SettlementMatch` per
        candidate row describing how it was resolved.
    """
    account = statement_account_of(statement.name)
    if not account:
        message = (
            f'No account in statement filename "{statement.name}"; settlement '
            "matching cannot tell two accounts' identical trades apart"
        )
        console_warning(message)
        audit.stage(
            'MATCH without an account: the filename "%s" names none',
            statement.name,
            level=logging.WARNING,
        )

    all_txns = txns.read()
    if not all_txns:  # pragma: no cover
        console_info("No transactions found to confirm")
        audit.stage("CONFIRM nothing: the folio holds no transactions")
        return 0, []

    existing_txns = [txn for txn in all_txns if txn.settle_calculated]
    actual_txns = [txn for txn in all_txns if not txn.settle_calculated]

    updates: list[Txn] = []
    results: list[SettlementMatch] = []
    pending: list[_PendingRow] = []
    for _, row in df.iterrows():
        try:
            statement_row = _extract_statement_row_data(row, account)
            if statement_row is None:
                continue
            matches = _match_transactions(existing_txns, statement_row)
            result, update, held = _resolve_statement_match(
                statement_row,
                matches,
                actual_txns,
            )
            results.append(result)
            pending.append(held)
            if update is not None:
                updates.append(update)
        except (ValueError, TypeError) as e:
            console_warning(f"Skipping invalid statement row: {e}")
            pending.append(
                _PendingRow(AuditMark.UNRESOLVED, f"invalid statement row: {e}"),
            )
            continue

    confirmed = txns.update(updates)
    if results:
        console_info(f"Matched {len(updates)} out of {len(results)} candidates.")
        audit.stage(
            "CONFIRM %d of %d settlement date(s)%s",
            len(updates),
            len(results),
            f"; {audit.describe_backup(current_backup())}" if updates else "",
        )
    for held in pending:
        audit.row(held.mark, held.text, level=held.level)
    return confirmed, results


def _resolve_statement_match(
    statement_row: _StatementRow,
    matches: list[Txn],
    actual_txns: list[Txn],
) -> tuple[SettlementMatch, Txn | None, _PendingRow]:
    """Turn a row's candidate matches into an outcome, with an update for a hit.

    Args:
        statement_row: Fields extracted from the statement row.
        matches: Transactions with a calculated date that the row matched.
        actual_txns: Transactions whose settlement date is already actual.

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
        matched = matches[0]
        result.txn_id = matched.txn_id
        result.outcome = SettlementOutcome.CONFIRMED
        update = replace(
            matched,
            settle_date=statement_row.settlement_date,
            settle_calculated=False,
        )
        change = (
            f"{Column.Txn.TXN_ID} {result.txn_id}: {Column.Txn.SETTLE_DATE} "
            f"{matched.settle_date} -> "
            f"{statement_row.settlement_date}"
        )
        return result, update, _PendingRow(AuditMark.CHANGED, change, logging.INFO)

    described = (
        f"{result.action} {result.ticker} on {result.txn_date} for {result.amount}"
    )
    if len(matches) > 1:
        result.outcome = SettlementOutcome.AMBIGUOUS
        text = f"{len(matches)} matches for {described}, left unconfirmed"
        return result, None, _PendingRow(AuditMark.UNRESOLVED, text)

    already = _match_transactions(actual_txns, statement_row)
    if len(already) == 1:
        result.outcome = SettlementOutcome.ALREADY_CONFIRMED
        result.txn_id = already[0].txn_id
        result.candidates = 1
        held = _PendingRow(
            AuditMark.UNCHANGED,
            f"already confirmed: {described}",
            logging.INFO,
        )
    else:
        held = _PendingRow(AuditMark.UNRESOLVED, f"no match for {described}")
    return result, None, held


@dataclass(frozen=True)
class _PendingRow:
    """A CONFIRM row held back until the stage line, with its totals, is written."""

    mark: AuditMark
    text: str
    level: int = logging.WARNING


@dataclass(frozen=True)
class _StatementRow:
    """The fields of a statement row that settlement matching runs on.

    Attributes:
        settlement_date: Settlement date from the statement, as YYYY-MM-DD.
        action: Statement action code, such as BUY or SELL.
        ticker: Ticker spelled the way the folio stores it.
        units: Units named in the description, when it names any.
        txn_date: Transaction date named in the description, as YYYY-MM-DD.
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


_AMOUNT_TOLERANCE = float(CENT)
_UNITS_TOLERANCE = float(UNIT_EPSILON)


def _within(stored: Decimal | None, stated: float, tolerance: float) -> bool:
    """Whether a stored magnitude is the one a statement states; a blank is not."""
    return stored is not None and abs(abs(float(stored)) - stated) < tolerance


def _match_transactions(
    existing_txns: list[Txn],
    statement_row: _StatementRow,
) -> list[Txn]:
    """Find transactions matching the statement data.

    Args:
        existing_txns: Transactions to look through, such as those still
            carrying a calculated settlement date.
        statement_row: Fields extracted from one statement row.

    Returns:
        Every transaction the row matches.
    """
    units = statement_row.units

    def matches(txn: Txn) -> bool:
        return (
            str(txn.action) == statement_row.action
            and txn.txn_date == statement_row.txn_date
            and txn.ticker == statement_row.ticker
            and str(txn.currency) == statement_row.currency
            and (not statement_row.account or txn.account == statement_row.account)
            and _within(txn.amount, statement_row.amount, _AMOUNT_TOLERANCE)
            and (not units or units <= 0 or _within(txn.units, units, _UNITS_TOLERANCE))
        )

    return [txn for txn in existing_txns if matches(txn)]


_TRANSFER_OUT_PREFIX = "TRFOUT"
_TRANSFER_IN_PREFIX = "TRFIN"
_CASH_TRANSFER_DESCRIPTION_PREFIX = "money transfer"


def _transfer_action_for_code(action_code: str) -> str | None:
    """Map a statement transfer code to a TFR action, or None if it isn't one."""
    code = action_code.strip().upper()
    if code.startswith(_TRANSFER_OUT_PREFIX):
        return Action.TFR_OUT
    if code.startswith(_TRANSFER_IN_PREFIX):
        return Action.TFR_IN
    return None


def _is_cash_transfer(description: str) -> bool:
    """Report whether a transfer row is a bank cash transfer."""
    return description.strip().lower().startswith(_CASH_TRANSFER_DESCRIPTION_PREFIX)


def _build_transfer_transactions(
    df: pd.DataFrame,
    statement_path: Path,
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Build TFR_IN/TFR_OUT transactions from transfer rows in a statement.

    Returns:
        Candidate transactions (possibly empty), the transfer rows that could
        not be turned into a transaction (each with its reason), and the cash
        transfers left alone because the activities import already covers them.
    """
    account = statement_account_of(statement_path.name)
    rows: list[dict[str, object]] = []
    rejected: list[str] = []
    skipped: list[str] = []

    for _, row in df.iterrows():
        action = _transfer_action_for_code(str(row["transaction"]))
        if action is None:
            continue

        if _is_cash_transfer(str(row["description"])):
            skipped.append(f"{row['transaction']}, {row['date']}, {row['amount']}")
            continue

        settle_date = _normalize_date(row["date"])
        if not account or not settle_date:
            reason = "no account in filename" if not account else "unparseable date"
            console_warning(
                f'Rejected transfer in "{statement_path.name}": {reason} '
                f"({row['transaction']}, {row['date']})",
            )
            rejected.append(f"{row['transaction']}, {row['date']} ({reason})")
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


def _store_transfers(
    transfer_df: pd.DataFrame,
    rejected: list[str],
    skipped: list[str],
) -> ImportResults | None:
    """Write the TRANSFER stage, running its transactions through the import pipeline.

    Args:
        transfer_df: The transfer transactions built from the statement.
        rejected: Transfer rows that could not become a transaction.
        skipped: Cash transfers the activities import already covers.

    Returns:
        The pipeline's results, or None when there was nothing to store.
    """
    found = len(transfer_df) + len(rejected) + len(skipped)
    if not found:
        return None
    audit.stage("TRANSFER %d transfer row(s) from the statement", found)
    for text in skipped:
        audit.row(AuditMark.UNCHANGED, f"cash transfer, left to the activities: {text}")
    for text in rejected:
        audit.row(AuditMark.REMOVED, text, level=logging.WARNING)
    if transfer_df.empty:
        return None
    with audit.nested():
        return insert_transactions(transfer_df, map_headers=False)
