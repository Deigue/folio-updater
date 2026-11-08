"""Add command for the folio CLI.

Handles manually adding a single transaction to the folio. Useful for actions
that brokers rarely report in their downloadable activity (SPLIT, ROC), or for
deliberate corrections. Routes the transaction through the same preparation
pipeline used by imports, so validation, duplicate detection, settlement date
calculation and audit logging all behave identically.
"""

from __future__ import annotations

import logging
from contextlib import nullcontext
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd
import typer

from app import audit, bootstrap, get_config
from app.audit import AuditMark
from cli.commands.common import export_to_parquet
from db import IntegrityError, current_backup, txns
from db.helpers import audit_txns, generate_keys, txns_with_keys
from domain import (
    ACTION_PROFILES,
    ALWAYS_REQUIRED,
    SUPPORTED_CURRENCIES,
    TORONTO_TZ,
    TXN_ESSENTIALS,
    Action,
    Column,
)
from ingest import prepare_transactions
from ingest.validation import TransactionFormatter
from term import console_error, console_info, console_success, console_warning
from ui.transaction_table import show_transactions

if TYPE_CHECKING:
    from app.audit import AuditEntry
    from models import ImportResults, NewTxn, Txn

logger = logging.getLogger(__name__)

# Order fields are prompted in: when, where, then what and how much.
PROMPT_ORDER: tuple[str, ...] = (
    Column.Txn.TXN_DATE,
    Column.Txn.ACCOUNT,
    Column.Txn.CURRENCY,
    Column.Txn.TICKER,
    Column.Txn.AMOUNT,
    Column.Txn.PRICE,
    Column.Txn.UNITS,
)

PROMPT_LABELS: dict[str, str] = {
    Column.Txn.TXN_DATE: "Transaction date (YYYY-MM-DD)",
    Column.Txn.ACCOUNT: "Account",
    Column.Txn.CURRENCY: (
        f"Currency ({'/'.join(c.value for c in SUPPORTED_CURRENCIES)})"
    ),
    Column.Txn.TICKER: "Ticker",
    Column.Txn.AMOUNT: "Amount",
    Column.Txn.PRICE: "Price",
    Column.Txn.UNITS: "Units",
}

# SPLIT encodes the ratio as Price -> FROM, Units -> TO, which is not obvious.
SPLIT_LABELS: dict[str, str] = {
    Column.Txn.PRICE: "Price (units BEFORE the split, e.g. 1 for a 1:10 split)",
    Column.Txn.UNITS: "Units (units AFTER the split, e.g. 10 for a 1:10 split)",
}


def _normalize_action(value: str) -> str | None:
    """Normalize an action, applying the same aliases imports accept.

    Args:
        value: Raw action text from the CLI or a prompt.

    Returns:
        The canonical Action value, or None if it is not recognized.
    """
    action = value.strip().upper()
    action = TransactionFormatter.ACTION_ALIASES.get(action, action)
    try:
        return Action(action).value
    except ValueError:
        return None


def _resolve_action(action: str | None) -> str:
    """Resolve the action from the option, prompting until one is valid."""
    valid = ", ".join(a.value for a in Action)
    while True:
        text: str = action if action is not None else typer.prompt("Type")
        normalized = _normalize_action(text)
        if normalized is not None:
            return normalized
        console_error(f"Unknown type '{text}'. Valid types: {valid}")
        action = None


def _required_columns(action: str) -> list[str]:
    """Return the columns that must be supplied for the given action.

    Args:
        action: The canonical action value.

    Returns:
        Required column names, in the order they should be prompted for.
    """
    required = {*ALWAYS_REQUIRED, *ACTION_PROFILES[Action(action)].required}
    return [column for column in PROMPT_ORDER if column in required]


def _prompt_label(column: str, action: str) -> str:
    """Get the prompt label for a column, specialized by action where needed."""
    if action == Action.SPLIT and column in SPLIT_LABELS:
        return SPLIT_LABELS[column]
    return PROMPT_LABELS[column]


def _collect_fields(action: str, supplied: dict[str, str | None]) -> dict[str, str]:
    """Gather all required field values, prompting for whatever is missing.

    Args:
        action: The canonical action value.
        supplied: Values already provided through CLI options, keyed by column.

    Returns:
        Mapping of column name to raw string value.
    """
    fields: dict[str, str] = {
        column: value for column, value in supplied.items() if value is not None
    }
    required = _required_columns(action)
    missing = [column for column in required if column not in fields]

    if missing and action == Action.SPLIT:
        console_info(
            "SPLIT ratios are stored as Price=units BEFORE, Units=units AFTER.",
        )

    for column in missing:
        label = _prompt_label(column, action)
        if column == Column.Txn.TXN_DATE:
            today = datetime.now(TORONTO_TZ).strftime("%Y-%m-%d")
            fields[column] = typer.prompt(label, default=today)
        else:
            fields[column] = typer.prompt(label)

    return fields


def _parse_extra_fields(values: list[str] | None) -> dict[str, str]:
    """Parse repeated --set KEY=VALUE options into a mapping.

    Args:
        values: Raw --set arguments.

    Raises:
        typer.Exit: If an argument is not in KEY=VALUE form.

    Returns:
        Mapping of column name to value.
    """
    extras: dict[str, str] = {}
    for item in values or []:
        key, separator, value = item.partition("=")
        if not separator or not key.strip():
            console_error(f"Invalid --set value '{item}'. Expected KEY=VALUE.")
            raise typer.Exit(1)
        extras[key.strip()] = value
    return extras


def _build_dataframe(
    fields: dict[str, str],
    *,
    approve_duplicate: bool,
) -> pd.DataFrame:
    """Build the single-row DataFrame handed to the preparation pipeline.

    Every essential column is seeded, even when the action does not use it.
    Imports get that guarantee from header mapping; skipping mapping means
    providing it here, since duplicate key generation reads all of them.
    """
    row: dict[str, object] = dict.fromkeys(TXN_ESSENTIALS, pd.NA)
    row.update(fields)
    if approve_duplicate:
        config = get_config()
        row[config.duplicate_approval_column] = config.duplicate_approval_value
    return pd.DataFrame([row])


def _show_rejections(results: ImportResults) -> None:
    """Report why the transaction failed validation."""
    console_error("Transaction was rejected:")
    for _, row in results.invalid_df.iterrows():
        reason = row.get(Column.REJECTION_REASON, "Unknown reason")
        console_error(f"  {reason}")


def _find_existing_duplicates(prepared_df: pd.DataFrame) -> list[Txn]:
    """Find transactions already in the database matching the prepared row.

    Uses the same synthetic key the duplicate filter uses, so the rows shown are
    exactly the ones that caused the rejection.

    Args:
        prepared_df: The single-row prepared (formatted) DataFrame.

    Returns:
        Matching transactions from the database, empty if none.
    """
    return txns_with_keys(txns.read(), {generate_keys(prepared_df).iloc[0]})


def _confirm_duplicate(prepared_df: pd.DataFrame) -> bool:
    """Show the clashing transactions and ask whether to add anyway."""
    matches = _find_existing_duplicates(prepared_df)
    count = len(matches)
    console_warning(
        f"This transaction matches {count} existing transaction(s) in the folio:",
    )
    if matches:
        show_transactions(matches, max_rows=len(matches))

    return typer.confirm("Add it anyway as an intentional duplicate?", default=False)


def _insert_transaction(approved: NewTxn, entry: AuditEntry) -> Txn:
    """Store the prepared transaction and return it with its TxnId.

    Args:
        approved: The transaction ingest passed.
        entry: The audit entry the insert is recorded in.

    Raises:
        typer.Exit: If the database rejects the row.

    Returns:
        The stored transaction.
    """
    try:
        [txn_id] = txns.append([approved])
    except IntegrityError as e:
        console_error(f"Database rejected the transaction: {e}")
        entry.failed(f"the table refused the transaction: {e}")
        raise typer.Exit(1) from e

    stored = approved.stored_as(txn_id)
    audit.stage(
        "STORE 1 transaction (TxnId %d); %s",
        txn_id,
        audit.describe_backup(current_backup()),
    )
    audit_txns(AuditMark.STORED, [stored])
    return stored


def _prepare(fields: dict[str, str], *, approve_duplicate: bool) -> ImportResults:
    """Run the transaction through the import preparation pipeline."""
    df = _build_dataframe(fields, approve_duplicate=approve_duplicate)
    return prepare_transactions(df, map_headers=False)


def add_transaction(  # noqa: PLR0917
    action: str | None = None,
    date: str | None = None,
    account: str | None = None,
    currency: str | None = None,
    ticker: str | None = None,
    amount: str | None = None,
    price: str | None = None,
    units: str | None = None,
    fee: str | None = None,
    set_values: list[str] | None = None,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    """Add a single transaction to the folio.

    Args:
        action: Transaction action (BUY, SELL, SPLIT, ROC, ...).
        date: Transaction date in YYYY-MM-DD format.
        account: Account alias the transaction belongs to.
        currency: Transaction currency.
        ticker: Security ticker.
        amount: Total transaction amount.
        price: Price per unit (units BEFORE for SPLIT).
        units: Number of units (units AFTER for SPLIT).
        fee: Optional transaction fee.
        set_values: Repeated KEY=VALUE pairs for optional/custom columns.
        force: Insert without confirming a duplicate clash.
        dry_run: Validate and preview without writing.
    """
    bootstrap.reload_config()

    resolved_action = _resolve_action(action)
    supplied: dict[str, str | None] = {
        Column.Txn.TXN_DATE: date,
        Column.Txn.ACCOUNT: account,
        Column.Txn.CURRENCY: currency,
        Column.Txn.TICKER: ticker,
        Column.Txn.AMOUNT: amount,
        Column.Txn.PRICE: price,
        Column.Txn.UNITS: units,
    }

    fields = _collect_fields(resolved_action, supplied)
    fields[Column.Txn.ACTION] = resolved_action
    if fee is not None:
        fields[Column.Txn.FEE] = fee
    fields.update(_parse_extra_fields(set_values))

    with audit.muted() if dry_run else nullcontext(), audit.entry("ADD TXN") as entry:
        results = _prepare(fields, approve_duplicate=force)

        if not results.invalid_df.empty:
            _show_rejections(results)
            entry.failed("the transaction is invalid")
            raise typer.Exit(1)

        if not results.db_rejected_df.empty:
            if not _confirm_duplicate(results.db_rejected_df):
                console_info("Transaction not added.")
                entry.done("nothing added: the duplicate was declined")
                return
            results = _prepare(fields, approve_duplicate=True)

        if dry_run:
            console_info("Dry run - nothing was written to the folio.")
            show_transactions(results.approved, title="Would Add")
            return

        added = _insert_transaction(results.approved[0], entry)
        entry.done("1 added; folio holds %d transactions", txns.count())

    console_success(f"Added transaction (TxnId {added.txn_id})")
    show_transactions([added], title="Added Transaction")

    export_to_parquet()
