"""Generate deterministic mock data for the folio."""

import logging
import random
from datetime import datetime, timedelta, timezone

import pandas as pd

from src.constants import TXN_ESSENTIALS

logger = logging.getLogger(__name__)


def generate_transactions(ticker: str, num_transactions: int = 5) -> pd.DataFrame:
    """Generate a deterministic set of mock transactions for a given ticker.

    Args:
        ticker (str): The stock ticker symbol to generate transactions for.
        num_transactions (int, optional): Number of transaction to generate (Default 5)

    Returns:
        pd.DataFrame: A DataFrame with the mock transactions.
    """
    random.seed(ticker)  # Deterministic per ticker.
    end_date: datetime = datetime.now(tz=timezone.utc)
    transactions = []
    for _ in range(num_transactions):
        txn_date = (end_date - timedelta(days=num_transactions * 30)).strftime(
            "%Y-%m-%d",
        )
        action: str = random.choice(["BUY", "SELL"])  # noqa: S311
        currency = "USD"
        price = round(random.uniform(10, 500), 2)  # noqa: S311
        units = round(random.uniform(1, 100), 2)  # noqa: S311

        transaction = {
            "TxnDate": txn_date,
            "Action": action,
            "Amount": price * units,
            "$": currency,
            "Price": price,
            "Units": units,
            "Ticker": ticker,
        }
        transactions.append(transaction)

    return pd.DataFrame(transactions, columns=TXN_ESSENTIALS)
