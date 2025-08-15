"""
Generate deterministic mock data for the folio.
"""


import random
from datetime import datetime, timedelta
import logging
import pandas as pd
from src.constants import TXN_ESSENTIALS

logger = logging.getLogger(__name__)

def generate_transactions(ticker: str, n: int = 5) -> pd.DataFrame:
    """Return a DataFrame with `n` deterministic mock rows for `ticker`."""
    random.seed(ticker)  # deterministic per ticker
    today = datetime.today()
    txns = []
    for i in range(n):
        txn_date = (today - timedelta(days=(n - i) * 30)).strftime("%Y-%m-%d")
        action = random.choice(["BUY", "SELL"])
        currency = "USD"
        price = round(random.uniform(10, 500), 2)
        units = round(random.uniform(1, 100), 2)

        txn = {
            "TxnDate": txn_date,
            "Action": action,
            "Amount": price * units,
            "$": currency,
            "Price": price,
            "Units": units,
            "Ticker": ticker
        }
        txns.append(txn)

    return pd.DataFrame(txns, columns=TXN_ESSENTIALS)
