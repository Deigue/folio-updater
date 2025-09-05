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


class ColumnDefinition:
    """Column definition with type and constraints for database schema."""

    def __init__(self, name: str, sql_type: str, constraints: str = "") -> None:
        """Initialize column definition.

        Args:
            name: Column name
            sql_type: SQL data type (TEXT, REAL, INTEGER)
            constraints: Additional SQL constraints (CHECK, NOT NULL, etc.)
        """
        self.name = name
        self.sql_type = sql_type
        self.constraints = constraints

    def to_sql(self) -> str:
        """Convert to SQL column definition."""
        base = f'"{self.name}" {self.sql_type}'
        if self.constraints:
            return f"{base} {self.constraints}"
        return base


# Date pattern for YYYY-MM-DD format validation
DATE_PATTERN_YYYY_MM_DD = "[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]"

# Column definitions for the Txns table
TXN_COLUMN_DEFINITIONS = [
    ColumnDefinition(
        Column.Txn.TXN_DATE.value,
        "TEXT",
        (
            f'CHECK(length("{Column.Txn.TXN_DATE}") = 10 AND '
            f'"{Column.Txn.TXN_DATE}" GLOB "{DATE_PATTERN_YYYY_MM_DD}")'
        ),
    ),
    ColumnDefinition(
        Column.Txn.ACTION.value,
        "TEXT",
        f'CHECK("{Column.Txn.ACTION}" IN ({", ".join(repr(a.value) for a in Action)}))',
    ),
    ColumnDefinition(Column.Txn.AMOUNT.value, "REAL"),
    ColumnDefinition(
        Column.Txn.CURRENCY.value,
        "TEXT",
        (
            f'CHECK("{Column.Txn.CURRENCY}" IN '
            f"({', '.join(repr(c.value) for c in Currency)}))"
        ),
    ),
    ColumnDefinition(Column.Txn.PRICE.value, "REAL"),
    ColumnDefinition(Column.Txn.UNITS.value, "REAL"),
    ColumnDefinition(
        Column.Txn.TICKER.value,
        "TEXT",
        (
            f'CHECK("{Column.Txn.TICKER}" IS NULL OR ('
            f'"{Column.Txn.TICKER}" = UPPER("{Column.Txn.TICKER}") AND '
            f'length("{Column.Txn.TICKER}") > 0))'
        ),
    ),
]


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
