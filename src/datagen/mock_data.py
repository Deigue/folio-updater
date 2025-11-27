"""Generate deterministic mock data for the folio."""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta

import pandas as pd

from db import ActionValidationRules
from utils import TORONTO_TZ, TXN_ESSENTIALS, Action, Column, Currency

logger = logging.getLogger(__name__)
SEED_DATE: datetime = datetime(2025, 10, 1, tzinfo=TORONTO_TZ)
DEFAULT_TXN_COUNT: int = 12


def get_mock_data_date_range(
    num_transactions: int = DEFAULT_TXN_COUNT,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Get the date range for mock data generation.

    Args:
        num_transactions: Number of transactions per ticker

    Returns:
        Tuple of (start_date, end_date) for the mock data
    """
    end_date = pd.Timestamp(SEED_DATE)
    start_date = end_date - timedelta(days=num_transactions * 7)
    return start_date, end_date


def generate_transactions(
    ticker: str,
    num_transactions: int = DEFAULT_TXN_COUNT,
) -> pd.DataFrame:
    """Generate a deterministic set of mock transactions for a given ticker.

    Args:
        ticker (str): The stock ticker symbol to generate transactions for.
        num_transactions (int, optional): Number of transactions

    Returns:
        pd.DataFrame: A DataFrame with the mock transactions.
    """
    random.seed(ticker)  # Deterministic per ticker.
    end_date: datetime = SEED_DATE
    transactions = []
    actions = list(Action)
    currencies = list(Currency)
    for i in range(num_transactions):
        action = actions[i % len(actions)]
        currency = currencies[i % len(currencies)]
        rules = ActionValidationRules.get_rules_for_action(action.value)
        txn_date = (end_date - timedelta(days=(num_transactions - i) * 7)).strftime(
            "%Y-%m-%d",
        )
        price = round(random.uniform(10, 500), 2)  # noqa: S311
        units = round(random.uniform(1, 100), 2)  # noqa: S311
        amount = price * units
        amount = round(amount, 10)  # db schema supports up to 10 decimal places

        if action in [Action.BUY, Action.WITHDRAWAL]:
            amount = -amount
        elif action in [Action.SELL]:
            units = -units
        elif action in [Action.FXT, Action.FCH]:
            amount = random.choice([-1, 1]) * amount  # noqa: S311

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
