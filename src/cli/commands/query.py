"""Query command for the folio CLI."""

from __future__ import annotations

import logging

from app import bootstrap
from cli.selection import select_transactions
from term import announce
from ui.views.transactions import page_transactions

logger = logging.getLogger(__name__)


def query_transactions(terms: list[str]) -> None:
    """Query transactions from the database.

    Args:
        terms: TxnIds, or query terms using the `folio query` syntax.
    """
    bootstrap.reload_config()
    announce.info(f"Resolving selection terms: {terms}")
    selection = select_transactions(terms)
    announce.info(f"{selection.describe()}")

    if selection.missing_ids:
        missing = ", ".join(str(txn_id) for txn_id in selection.missing_ids)
        announce.info(f"No transaction with TxnId {missing}.")

    results_df = selection.transactions

    if results_df.empty:
        announce.info("No transactions found matching the criteria.")
    else:
        announce.info(f"Found {len(results_df)} matching transaction(s).")
        page_transactions(results_df, title="Query Results")
