"""Module for database utility functions."""

import pandas as pd

from utils.constants import TXN_ESSENTIALS


def format_transaction_summary(row: pd.Series) -> str:
    """Format a transaction row into a human-readable summary.

    Args:
        row: A pandas Series containing transaction data.

    Returns:
        A formatted string summarizing the transaction.
    """
    essential_parts = []
    for col in TXN_ESSENTIALS:
        value = row.get(col, "N/A")
        if pd.isna(value):
            value = "N/A"
        essential_parts.append(f"{col}={value}")

    return "|".join(essential_parts)
