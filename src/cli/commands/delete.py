"""Delete command for the folio CLI.

Removes transactions selected either by explicit TxnId or by the same query
terms `folio query` accepts.
The rows are previewed and confirmed before anything is executed, the folio is
backed up first, and every removal is recorded in the audit trail.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app import audit
from cli.commands.common import commit, confirm_selection, resolve_selection
from ingest.changes import DeletePlan
from term import console_info
from ui.transaction_table import page_transactions

if TYPE_CHECKING:
    from cli.selection import Selection


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
    selection: Selection = resolve_selection(terms, verb="delete")
    plan = DeletePlan(removed=selection.transactions)
    page_transactions(plan.removed, title="Transactions to Delete")

    if dry_run:
        console_info("Dry run - nothing was written to the folio.")
        return

    if not confirm_selection(len(plan.removed), verb="delete", force=force):
        console_info("No transactions deleted.")
        return

    with audit.entry("DELETE TXNS %s", selection.describe()) as entry:
        commit(plan, entry)
