"""Generate deterministic mock data for the folio."""

import logging
import random
from datetime import datetime, timedelta

import pandas as pd

from db.formatters import ActionValidationRules
from utils.constants import TORONTO_TZ, TXN_ESSENTIALS, Action, Column, Currency

logger = logging.getLogger(__name__)


def generate_transactions(ticker: str, num_transactions: int = 10) -> pd.DataFrame:
    """Generate a deterministic set of mock transactions for a given ticker.

    Args:
        ticker (str): The stock ticker symbol to generate transactions for.
        num_transactions (int, optional): Number of transactions (Default 10)

    Returns:
        pd.DataFrame: A DataFrame with the mock transactions.
    """
    random.seed(ticker)  # Deterministic per ticker.
    end_date: datetime = datetime.now(TORONTO_TZ)
    transactions = []
    actions = list(Action)
    currencies = list(Currency)
    for i in range(num_transactions):
        action = actions[i % len(actions)]
        currency = currencies[i % len(currencies)]
        rules = ActionValidationRules.get_rules_for_action(action.value)
        txn_date = (end_date - timedelta(days=(num_transactions - i) * 30)).strftime(
            "%Y-%m-%d",
        )
        price = round(random.uniform(10, 500), 2)  # noqa: S311
        units = round(random.uniform(1, 100), 2)  # noqa: S311
        amount = price * units

        # Start with all fields present
        transaction = {
            Column.Txn.TXN_DATE: txn_date,
            Column.Txn.ACTION: action,
            Column.Txn.AMOUNT: amount,
            Column.Txn.CURRENCY: currency,
            Column.Txn.PRICE: price,
            Column.Txn.UNITS: units,
            Column.Txn.TICKER: ticker,
            Column.Txn.ACCOUNT: "MOCK-ACCOUNT",
        }

        # For some transactions, purposely omit optionals to simulate real data and
        # test validation.
        if rules["optional_fields"]:
            for field in rules["optional_fields"]:
                transaction[getattr(Column.Txn, field.upper())] = None

        transactions.append(transaction)

    return pd.DataFrame(transactions, columns=TXN_ESSENTIALS)
