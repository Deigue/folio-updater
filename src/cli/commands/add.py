"""Add command for the folio CLI.

Handles manually adding a single transaction to the folio. Useful for actions
that brokers rarely report in their downloadable activity (SPLIT, ROC), or for
deliberate corrections. Routes the transaction through the same preparation
pipeline used by imports, so validation, duplicate detection, settlement date
calculation and audit logging all behave identically.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
from typing import TYPE_CHECKING

import typer

from app import audit
from cli.commands.common import commit, refusal
from domain import (
    ACTION_PROFILES,
    ALWAYS_REQUIRED,
    SUPPORTED_CURRENCIES,
    TORONTO_TZ,
    Action,
    Column,
    resolve_action,
)
from ingest.changes import AddPlan, PlanRefusedError, plan_add
from term import console_error, console_info, console_warning
from ui.transaction_table import show_transactions

if TYPE_CHECKING:
    from app.audit import AuditEntry


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


def _prompt_action(action: str | None) -> str:
    """Resolve the action from the option, prompting until one is valid."""
    valid = ", ".join(a.value for a in Action)
    while True:
        text: str = action if action is not None else typer.prompt("Type")
        normalized = resolve_action(text)
        if normalized is not None:
            return normalized.value
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


def _plan(
    fields: dict[str, str],
    entry: AuditEntry,
    *,
    approve_duplicate: bool,
) -> AddPlan:
    """Work out the transaction, or report why it is rejected and stop.

    Raises:
        typer.Exit: If the transaction is invalid.
    """
    try:
        return plan_add(fields, approve_duplicate=approve_duplicate)
    except PlanRefusedError as refused:
        entry.failed("the transaction is invalid")
        raise refusal(refused) from refused


def _confirm_duplicate(plan: AddPlan) -> bool:
    """Show the clashing transactions and ask whether to add anyway."""
    console_warning(
        f"This transaction matches {len(plan.duplicates)} existing transaction(s) "
        "in the folio:",
    )
    show_transactions(plan.duplicates, max_rows=len(plan.duplicates))
    return typer.confirm("Add it anyway as an intentional duplicate?", default=False)


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
    resolved_action = _prompt_action(action)
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
        plan = _plan(fields, entry, approve_duplicate=force)
        if plan.duplicates:
            if not _confirm_duplicate(plan):
                console_info("Transaction not added.")
                entry.done("nothing added: the duplicate was declined")
                return
            plan = _plan(fields, entry, approve_duplicate=True)

        if dry_run:
            console_info("Dry run - nothing was written to the folio.")
            show_transactions([plan.new], title="Would Add")
            return

        commit(plan, entry)
