"""Query command for the folio CLI."""

from __future__ import annotations

import logging

from app import bootstrap
from cli.display import page_transactions
from cli.selection import select_transactions
from utils.log_console import info_both

logger = logging.getLogger(__name__)


def query_transactions(terms: list[str]) -> None:
    """Query transactions from the database.

    Args:
        terms: TxnIds, or query terms using the `folio query` syntax.
    """
    bootstrap.reload_config()
    info_both(f"Resolving selection terms: {terms}")
    selection = select_transactions(terms)
    info_both(f"{selection.describe()}")

    if selection.missing_ids:
        missing = ", ".join(str(txn_id) for txn_id in selection.missing_ids)
        info_both(f"No transaction with TxnId {missing}.")

    results_df = selection.transactions

    if results_df.empty:
        info_both("No transactions found matching the criteria.")
    else:
        info_both(f"Found {len(results_df)} matching transaction(s).")
        page_transactions(results_df, title="Query Results")
