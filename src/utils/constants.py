"""Non-user-configurable defaults/constants used throughout the app."""

# Internal transaction fields that are essential for processing
from __future__ import annotations

from enum import StrEnum
from zoneinfo import ZoneInfo

TORONTO_TZ = ZoneInfo("America/Toronto")


class Currency(StrEnum):
    """Currency codes."""

    USD = "USD"
    CAD = "CAD"
    EUR = "EUR"


class Action(StrEnum):
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


class TransactionContext(StrEnum):
    """Context for transaction display to control column visibility."""

    IMPORT = "import"  # Import context: hide TxnId and SettleDate
    SETTLEMENT = "settlement"  # Settlement context: show all columns including TxnId
    GENERAL = "general"  # General context: show all columns


class Table(StrEnum):
    """Table names."""

    TXNS = "Txns"
    FX = "FX"


class Column(StrEnum):
    """Constants for column names."""

    REJECTION_REASON = "Rejection_Reason"

    class Txn(StrEnum):
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

    class Ticker(StrEnum):
        """Ticker columns."""

        TICKER = "Ticker"

    class FX(StrEnum):
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
        Column.Txn.TXN_ID,
        "INTEGER",
        "PRIMARY KEY AUTOINCREMENT",
    ),
    ColumnDefinition(
        Column.Txn.TXN_DATE,
        "TEXT",
        (
            f'CHECK(length("{Column.Txn.TXN_DATE}") = 10 AND '
            f'"{Column.Txn.TXN_DATE}" GLOB "{DATE_PATTERN_YYYY_MM_DD}")'
        ),
    ),
    ColumnDefinition(
        Column.Txn.ACTION,
        "TEXT",
        f'CHECK("{Column.Txn.ACTION}" IN ({", ".join(repr(str(a)) for a in Action)}))',
    ),
    ColumnDefinition(Column.Txn.AMOUNT, NUMERIC_PRECISION),
    ColumnDefinition(
        Column.Txn.CURRENCY,
        "TEXT",
        (
            f'CHECK("{Column.Txn.CURRENCY}" IN '
            f"({', '.join(repr(str(c)) for c in Currency)}))"
        ),
    ),
    ColumnDefinition(Column.Txn.PRICE, NUMERIC_PRECISION),
    ColumnDefinition(Column.Txn.UNITS, NUMERIC_PRECISION),
    ColumnDefinition(
        Column.Txn.TICKER,
        "TEXT",
        (
            f'CHECK("{Column.Txn.TICKER}" IS NULL OR ('
            f'"{Column.Txn.TICKER}" = UPPER("{Column.Txn.TICKER}") AND '
            f'length("{Column.Txn.TICKER}") > 0))'
        ),
    ),
    ColumnDefinition(
        Column.Txn.ACCOUNT,
        "TEXT",
        (
            f'CHECK("{Column.Txn.ACCOUNT}" IS NOT NULL AND '
            f'length("{Column.Txn.ACCOUNT}") > 0)'
        ),
    ),
    ColumnDefinition(
        Column.Txn.SETTLE_DATE,
        "TEXT",
        (
            f'CHECK(length("{Column.Txn.SETTLE_DATE}") = 10 AND '
            f'"{Column.Txn.SETTLE_DATE}" GLOB "{DATE_PATTERN_YYYY_MM_DD}")'
        ),
    ),
    ColumnDefinition(
        Column.Txn.SETTLE_CALCULATED,
        "INTEGER",
        f'CHECK("{Column.Txn.SETTLE_CALCULATED}" IN (0, 1))',
    ),
]

FX_COLUMN_DEFINITIONS = [
    ColumnDefinition(
        Column.FX.DATE,
        "TEXT",
        (
            f'PRIMARY KEY CHECK(length("{Column.FX.DATE}") = 10 AND '
            f'"{Column.FX.DATE}" GLOB "{DATE_PATTERN_YYYY_MM_DD}")'
        ),
    ),
    ColumnDefinition(Column.FX.FXUSDCAD, NUMERIC_PRECISION, "NOT NULL"),
    ColumnDefinition(Column.FX.FXCADUSD, NUMERIC_PRECISION, "NOT NULL"),
]


TXN_ESSENTIALS: list[str] = [
    Column.Txn.TXN_DATE,  # Date of transaction
    Column.Txn.ACTION,  # BUY/SELL
    Column.Txn.AMOUNT,  # Total amount (Price * Units)
    Column.Txn.CURRENCY,  # Currency
    Column.Txn.PRICE,  # Price per unit
    Column.Txn.UNITS,  # Number of units
    Column.Txn.TICKER,  # Stock or ETF ticker
    Column.Txn.ACCOUNT,  # Account alias where transaction occurred
]

# Default tickers for newly created folio file
DEFAULT_TICKERS = ["SPY", "AAPL", "O", "REI-UN.TO", "RY.TO"]
