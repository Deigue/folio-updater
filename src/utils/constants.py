"""Non-user-configurable defaults/constants used throughout the app."""

# Internal transaction fields that are essential for processing
from __future__ import annotations

import sqlite3
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import NotImplementedType


class AutoStrEnum(Enum):
    """Enum that automatically creates a string representation of the enum value."""

    def __str__(self) -> str:
        """Return a string representation of the enum value."""
        return self.value

    def __conform__(self, protocol: sqlite3.PrepareProtocol) -> str:  # pragma: no cover
        """Conform to string for sqlite3."""
        if protocol is sqlite3.PrepareProtocol:
            return self.value
        msg = f"Cannot conform {self} to {protocol}"
        raise TypeError(msg)

    def __hash__(self) -> int:
        """Hash the enum value."""
        return hash(self.value)

    def __eq__(self, other: object) -> bool | NotImplementedType:  # pragma: no cover
        """Compare the enum value to another enum or a string.

        If the `other` argument is an instance of `AutoStrEnum`, compare the
        underlying values.

        If the `other` argument is a string, compare the string representations
        of the enum values.

        If the `other` argument is any other type, return `NotImplemented`.
        """
        if isinstance(other, AutoStrEnum):
            return self.value == other.value
        if isinstance(other, str):
            return self.value == other
        return NotImplemented

    def __repr__(self) -> str:  # pragma: no cover
        """Representation of this enum should be a string."""
        return self.value


class Currency(AutoStrEnum):
    """Currency codes."""

    USD = "USD"
    CAD = "CAD"


class Action(AutoStrEnum):
    """Transaction actions."""

    BUY = "BUY"
    SELL = "SELL"


class Table(AutoStrEnum):
    """Table names."""

    TXNS = "Txns"


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

    class Ticker(AutoStrEnum):
        """Ticker columns."""

        TICKER = "Ticker"


TXN_ESSENTIALS: list[str] = [
    str(col)
    for col in [
        Column.Txn.TXN_DATE,  # Date of transaction
        Column.Txn.ACTION,  # BUY/SELL
        Column.Txn.AMOUNT,  # Total amount (Price * Units)
        Column.Txn.CURRENCY,  # Currency
        Column.Txn.PRICE,  # Price per unit
        Column.Txn.UNITS,  # Number of units
        Column.Txn.TICKER,  # Stock or ETF ticker
    ]
]

# Default tickers for newly created folio file
DEFAULT_TICKERS = ["SPY", "AAPL"]
