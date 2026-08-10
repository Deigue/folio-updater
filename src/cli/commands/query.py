"""Query command for the folio CLI."""

from __future__ import annotations

import logging

from app import bootstrap
from cli.display import page_transactions
from cli.query_parser import ParsedQuery, parse_query_terms
from cli.selection import get_transactions_by_filters
from utils.log_console import info_both

logger = logging.getLogger(__name__)


def query_transactions(terms: list[str]) -> None:
    """Query transactions from the database.

    Args:
        terms: A list of query terms from the CLI.
    """
    bootstrap.reload_config()
    info_both(f"Parsing query terms: {terms}")
    query: ParsedQuery = parse_query_terms(terms)
    info_both(f"{query}")

    results_df = get_transactions_by_filters(query)

    if results_df.empty:
        info_both("No transactions found matching the criteria.")
    else:
        info_both(f"Found {len(results_df)} matching transaction(s).")
        page_transactions(results_df, title="Query Results")
