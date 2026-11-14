"""Delete command for the folio CLI.

Removes transactions selected either by explicit TxnId or by the same query
terms `folio query` accepts.
The rows are previewed and confirmed before anything is executed, the folio is
backed up first, and every removal is recorded in the audit trail.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import typer

from app import audit, bootstrap
from app.audit import AuditMark, audit_txns
from cli.commands.common import (
    confirm_selection,
    export_to_parquet,
    resolve_selection,
)
from db import current_backup, txns
from term import console_error, console_info, console_success
from ui.transaction_table import page_transactions

if TYPE_CHECKING:
    from cli.selection import Selection
    from models import Txn

logger = logging.getLogger(__name__)


def _log_removed(transactions: list[Txn]) -> None:
    """Write the REMOVE stage: the backup that undoes it, then each removed row."""
    audit.stage(
        "REMOVE %d transaction(s); %s",
        len(transactions),
        audit.describe_backup(current_backup()),
    )
    audit_txns(AuditMark.REMOVED, transactions)


def delete_transactions(
    terms: list[str],
    *,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    """Delete transactions from the folio.

    Args:
        terms: TxnIds, or query terms using the `folio query` syntax.
        force: Delete without asking for confirmation.
        dry_run: Preview the matched transactions without deleting.
    """
    bootstrap.reload_config()

    selection: Selection = resolve_selection(terms, verb="delete")
    matched = selection.transactions
    page_transactions(matched, title="Transactions to Delete")

    if dry_run:
        console_info("Dry run - nothing was written to the folio.")
        return

    if not confirm_selection(
        len(matched),
        verb="delete",
        force=force,
    ):
        console_info("No transactions deleted.")
        return

    with audit.entry("DELETE TXNS %s", selection.describe()) as entry:
        deleted = txns.delete(selection.txn_ids)
        _log_removed(matched)
        if deleted == len(matched):
            entry.done("%d deleted; folio holds %d transactions", deleted, txns.count())
        else:
            entry.failed(f"{len(matched)} transaction(s) matched but {deleted} deleted")

    if deleted != len(matched):
        console_error(
            f"Deleted {deleted} of {len(matched)} transaction(s); "
            "the folio is unchanged for the rest. See the log for details.",
        )
        raise typer.Exit(1)

    console_success(f"Deleted {deleted} transaction(s)")
    export_to_parquet()
