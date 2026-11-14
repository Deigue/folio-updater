"""Query command for the folio CLI."""

from __future__ import annotations

import logging

import typer

from app import bootstrap
from cli.selection import SelectionError, select_transactions
from db import txns
from engine.query_totals import query_totals, resolved_fees
from term import announce, console_error
from ui.transaction_table import page_transactions

logger = logging.getLogger(__name__)


def query_transactions(terms: list[str]) -> None:
    """Query transactions from the database.

    Args:
        terms: TxnIds, or query terms using the `folio query` syntax.

    Raises:
        typer.Exit: If the query could not run.
    """
    bootstrap.reload_config()
    announce.info(f"Resolving selection terms: {terms}")
    try:
        selection = select_transactions(terms)
    except SelectionError as error:
        console_error(str(error))
        raise typer.Exit(1) from error
    announce.info(f"{selection.describe()}")

    if selection.missing_ids:
        missing = ", ".join(str(txn_id) for txn_id in selection.missing_ids)
        announce.info(f"No transaction with TxnId {missing}.")

    results = selection.transactions

    if results:
        announce.info(f"Found {len(results)} matching transaction(s).")
        charged = {txn.account for txn in results if txn.fee}
        fees = resolved_fees(charged, txns.read() if charged else [])
        totals = query_totals(results, fees)
        page_transactions(results, title="Query Results", totals=totals)
    else:
        announce.info("No transactions found matching the criteria.")
