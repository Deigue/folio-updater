"""Test data creation helpers for high-performance testing."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from utils.constants import TORONTO_TZ, Column

from .dataframe_cache import register_test_dataframe

if TYPE_CHECKING:
    from pathlib import Path


def create_transaction_data(
    file_path: Path,
    sheet_name: str = "Txns",
) -> pd.DataFrame:
    """Create test transaction data and register it for the given file path.

    Args:
        file_path: The path key to register the data under
        sheet_name: Sheet name for Excel-like behavior

    Returns:
        The created DataFrame for reference
    """
    # Use dynamic dates based on today to ensure they're always in the future
    today = datetime.now(TORONTO_TZ).date()
    date1 = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    date2 = (today + timedelta(days=2)).strftime("%Y-%m-%d")
    random.seed(file_path.name)
    # Generate randomized price and unit values for unique transactions
    price1 = round(random.uniform(50.0, 500.0), 2)  # noqa: S311
    price2 = round(random.uniform(50.0, 500.0), 2)  # noqa: S311
    units1 = round(random.uniform(1.0, 100.0), 2)  # noqa: S311
    units2 = round(random.uniform(1.0, 100.0), 2)  # noqa: S311
    test_data = {
        Column.Txn.TXN_DATE: [date1, date2],
        Column.Txn.ACTION: ["BUY", "SELL"],
        Column.Txn.AMOUNT: [price1 * units1, price2 * units2],
        Column.Txn.CURRENCY: ["USD", "USD"],
        Column.Txn.PRICE: [price1, price2],
        Column.Txn.UNITS: [units1, units2],
        Column.Txn.TICKER: ["AAPL", "MSFT"],
        Column.Txn.ACCOUNT: ["TEST-ACCOUNT", "TEST-ACCOUNT"],
    }

    df = pd.DataFrame(test_data)
    register_test_dataframe(file_path, df, sheet_name)
    return df
