"""Non-user-configurable defaults/constants used throughout the app."""

# Internal transaction fields that are essential for processing
from __future__ import annotations

import sqlite3
from enum import Enum
from typing import TYPE_CHECKING

from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from types import NotImplementedType

TORONTO_TZ = ZoneInfo("America/Toronto")


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

    BUY = "BUY"  # This represents buying a stock
    SELL = "SELL"  # This represents selling a stock
    DIVIDEND = "DIVIDEND"  # Acquired dividends from stocks
    BRW = "BRW"  # Borrowing activity, reflecting Norbert's Gambit journaling
    CONTRIBUTION = "CONTRIBUTION"  # Contribute money into the portfolio
    FCH = "FCH"  # Fee Charges, Interest earned, RSU vestments, Non-contribution income
    FXT = "FXT"  # Foreign Exchange Trades
    ROC = "ROC"  # Return of Capital transactions (reduces cost basis)
    SPLIT = "SPLIT"  # Designates stock splits (Price->FROM, Units->TO)
    WITHDRAWAL = "WITHDRAWAL"  # Take out money from the portfolio


class Table(AutoStrEnum):
    """Table names."""

    TXNS = "Txns"
    FX = "FX"


class Column(AutoStrEnum):
    """Constants for column names."""

    class Txn(AutoStrEnum):
        """Transaction columns."""

        TXN_ID = "TxnId"
        TXN_DATE = "TxnDate"
        ACTION = "Action"
        AMOUNT = "Amount"
        CURRENCY = "$"
        PRICE = "Price"
        UNITS = "Units"
        TICKER = "Ticker"
        ACCOUNT = "Account"
        FEE = "Fee"
        SETTLE_DATE = "SettleDate"
        SETTLE_CALCULATED = "SettleCalculated"

    class Ticker(AutoStrEnum):
        """Ticker columns."""

        TICKER = "Ticker"

    class FX(AutoStrEnum):
        """Forex rate columns."""

        DATE = "Date"
        FXUSDCAD = "FXUSDCAD"
        FXCADUSD = "FXCADUSD"


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

# SQL type for numeric columns with precision
NUMERIC_PRECISION = "NUMERIC(20,10)"

# Column definitions for the Txns table
TXN_COLUMN_DEFINITIONS = [
    ColumnDefinition(
        Column.Txn.TXN_ID.value,
        "INTEGER",
        "PRIMARY KEY AUTOINCREMENT",
    ),
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
    ColumnDefinition(Column.Txn.AMOUNT.value, NUMERIC_PRECISION),
    ColumnDefinition(
        Column.Txn.CURRENCY.value,
        "TEXT",
        (
            f'CHECK("{Column.Txn.CURRENCY}" IN '
            f"({', '.join(repr(c.value) for c in Currency)}))"
        ),
    ),
    ColumnDefinition(Column.Txn.PRICE.value, NUMERIC_PRECISION),
    ColumnDefinition(Column.Txn.UNITS.value, NUMERIC_PRECISION),
    ColumnDefinition(
        Column.Txn.TICKER.value,
        "TEXT",
        (
            f'CHECK("{Column.Txn.TICKER}" IS NULL OR ('
            f'"{Column.Txn.TICKER}" = UPPER("{Column.Txn.TICKER}") AND '
            f'length("{Column.Txn.TICKER}") > 0))'
        ),
    ),
    ColumnDefinition(
        Column.Txn.ACCOUNT.value,
        "TEXT",
        (
            f'CHECK("{Column.Txn.ACCOUNT}" IS NOT NULL AND '
            f'length("{Column.Txn.ACCOUNT}") > 0)'
        ),
    ),
    ColumnDefinition(
        Column.Txn.SETTLE_DATE.value,
        "TEXT",
        (
            f'CHECK(length("{Column.Txn.SETTLE_DATE}") = 10 AND '
            f'"{Column.Txn.SETTLE_DATE}" GLOB "{DATE_PATTERN_YYYY_MM_DD}")'
        ),
    ),
    ColumnDefinition(
        Column.Txn.SETTLE_CALCULATED.value,
        "INTEGER",
        f'CHECK("{Column.Txn.SETTLE_CALCULATED}" IN (0, 1))',
    ),
]

FX_COLUMN_DEFINITIONS = [
    ColumnDefinition(
        Column.FX.DATE.value,
        "TEXT",
        (
            f'PRIMARY KEY CHECK(length("{Column.FX.DATE}") = 10 AND '
            f'"{Column.FX.DATE}" GLOB "{DATE_PATTERN_YYYY_MM_DD}")'
        ),
    ),
    ColumnDefinition(Column.FX.FXUSDCAD.value, NUMERIC_PRECISION, "NOT NULL"),
    ColumnDefinition(Column.FX.FXCADUSD.value, NUMERIC_PRECISION, "NOT NULL"),
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
        Column.Txn.ACCOUNT,  # Account alias where transaction occurred
    ]
]

# Default tickers for newly created folio file
DEFAULT_TICKERS = ["SPY", "AAPL", "O", "REI-UN.TO", "RY.TO"]
