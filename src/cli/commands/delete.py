"""Delete command for the folio CLI.

Removes transactions selected either by explicit TxnId or by the same query
terms `folio query` accepts.
The rows are previewed and confirmed before anything is executed, the folio is
backed up first, and every removal is recorded in the importer audit log.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app import bootstrap
from cli.commands.common import (
    audit_footer,
    backup_folio,
    confirm_selection,
    export_to_parquet,
    resolve_selection,
)
from db.helpers import format_transaction_summary
from db.queries import delete_rows, get_connection
from ui import console_info, console_success
from ui.views.transactions import page_transactions
from utils import Column, Table, get_import_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pandas as pd

    from cli.selection import Selection

logger = logging.getLogger(__name__)
import_logger = get_import_logger()


def _delete_by_ids(txn_ids: Sequence[int]) -> int:
    """Delete transactions by TxnId.

    Args:
        txn_ids: TxnIds of the transactions to remove.

    Returns:
        Number of rows deleted.
    """
    placeholders = ",".join("?" * len(txn_ids))
    with get_connection() as conn:
        return delete_rows(
            conn,
            Table.TXNS,
            where=f'"{Column.Txn.TXN_ID}" IN ({placeholders})',
            params=list(txn_ids),
        )


def _log_delete(selection: Selection, transactions: pd.DataFrame) -> None:
    """Record the deletion in the shared import audit log."""
    import_logger.info("DELETE TXN (manual, %s)", selection.describe())
    for _, row in transactions.iterrows():
        import_logger.info(" - %s", format_transaction_summary(row))
    txn_ids = ", ".join(str(txn_id) for txn_id in selection.txn_ids)
    import_logger.info(
        "DONE: %d transaction(s) deleted (TxnIds %s)",
        len(transactions),
        txn_ids,
    )
    audit_footer()


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

    backup_folio()
    deleted = _delete_by_ids(selection.txn_ids)
    _log_delete(selection, matched)

    console_success(f"Deleted {deleted} transaction(s)")
    export_to_parquet()
