"""Non-user-configurable defaults/constants used throughout the app."""

# Internal transaction fields that are essential for processing
from __future__ import annotations

from enum import Enum


class AutoStrEnum(Enum):
    """Enum that automatically creates a string representation of the enum value."""

    def __str__(self) -> str:
        """Return a string representation of the enum value."""
        return self.value


class Currency(AutoStrEnum):
    """Currency codes."""

    USD = "USD"
    CAD = "CAD"


class Action(AutoStrEnum):
    """Transaction actions."""

    BUY = "BUY"
    SELL = "SELL"


class Column(AutoStrEnum):
    """Constants for column names."""

    class Txn(AutoStrEnum):
        """Transaction columns."""

        TXN_DATE = "TxnDate"
        ACTION = "Action"
        AMOUNT = "Amount"
        CURRENCY = "$"
        PRICE = "Price"
        UNITS = "Units"
        TICKER = "Ticker"


TXN_ESSENTIALS = [
    Column.Txn.TXN_DATE,  # Date of transaction
    Column.Txn.ACTION,  # BUY/SELL
    Column.Txn.AMOUNT,  # Total amount (Price * Units)
    Column.Txn.CURRENCY,  # Currency
    Column.Txn.PRICE,  # Price per unit
    Column.Txn.UNITS,  # Number of units
    Column.Txn.TICKER,  # Stock or ETF ticker
]

# Default tickers for newly created folio file
DEFAULT_TICKERS = ["SPY", "AAPL"]
