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
    CONTRIBUTION = "CONTRIBUTION"  # New money into the portfolio, from outside it
    FCH = "FCH"  # Financial Charge: fees, interest, RSU income, other cash adjustments
    FXT = "FXT"  # Foreign Exchange Trades
    ROC = "ROC"  # Return of Capital: reduces cost basis, a reclassification not cash
    SPLIT = "SPLIT"  # Designates stock splits (Price->FROM, Units->TO)
    TFR_IN = "TFR_IN"  # Cash or units arriving from another account you own
    TFR_OUT = "TFR_OUT"  # Cash or units leaving for another account you own
    WITHDRAWAL = "WITHDRAWAL"  # Money leaving the portfolio, to outside it


class Sign(StrEnum):
    """Required sign for a numeric transaction field."""

    POSITIVE = "positive"  # Value must be > 0 (cash in, units acquired)
    NEGATIVE = "negative"  # Value must be < 0 (cash out, units disposed)


class AccountType(StrEnum):
    """Tax treatment of an account, inferred from its name."""

    NON_REGISTERED = "NON_REGISTERED"
    MARGIN = "MARGIN"
    CORPORATE = "CORPORATE"
    TFSA = "TFSA"
    RRSP = "RRSP"
    RRIF = "RRIF"
    RESP = "RESP"
    FHSA = "FHSA"
    LIRA = "LIRA"
    UNKNOWN = "UNKNOWN"


# Account types whose dispositions are taxable, and therefore the only ones
# where realized gains and superficial losses matter.
TAXABLE_ACCOUNT_TYPES = frozenset(
    {AccountType.NON_REGISTERED, AccountType.MARGIN, AccountType.CORPORATE},
)

# Names a broker or a user might give an account that mean an AccountType
ACCOUNT_TYPE_ALIASES: dict[str, AccountType] = {
    "NONREG": AccountType.NON_REGISTERED,
    "NREG": AccountType.NON_REGISTERED,
    "NON-REG": AccountType.NON_REGISTERED,
    "NONREGISTERED": AccountType.NON_REGISTERED,
    "NON-REGISTERED": AccountType.NON_REGISTERED,
    "PERSONAL": AccountType.NON_REGISTERED,
    "CASH": AccountType.NON_REGISTERED,
    "TAXABLE": AccountType.NON_REGISTERED,
}


class FeeConvention(StrEnum):
    """Whether a txn `Amount` on a trade already contains the commission.

    QuestTrade reports `Amount` net of the fee (INCLUDED);
    IBKR and Wealthsimple report it gross, with the fee charged separately (EXCLUDED).
    AUTO reconciles each account's rows against `Price * Units` to decide.
    """

    AUTO = "auto"
    INCLUDED = "included"
    EXCLUDED = "excluded"


class Scope(StrEnum):
    """The pool a cost base is accumulated over."""

    ACCOUNT = "acct"
    TYPE = "type"
    FOLIO = "folio"


class Impact(StrEnum):
    """What a transaction does to the cost base."""

    ACB = "ACB"  # Moves units or cost base (BUY, SELL, ROC, SPLIT, transfers)
    INCOME = "INCOME"  # Reported as income, never touches ACB (DIVIDEND, FCH)
    NONE = "NONE"  # Cash-only (CONTRIBUTION, WITHDRAWAL, FXT)


class WarningCode(StrEnum):
    """Diagnostics a replay can raise against a row, an account or a pool."""

    OVERSELL = "OVERSELL"
    NEGATIVE_FINAL_POSITION = "NEGATIVE_FINAL_POSITION"
    CASH_NEGATIVE = "CASH_NEGATIVE"
    UNRECORDED_CASH_TRANSFER = "UNRECORDED_CASH_TRANSFER"
    DUPLICATE_SPLIT = "DUPLICATE_SPLIT"
    SPLIT_SCOPE_MISMATCH = "SPLIT_SCOPE_MISMATCH"
    SPLIT_WITHOUT_POSITION = "SPLIT_WITHOUT_POSITION"
    SPLIT_RATIO_IMPLAUSIBLE = "SPLIT_RATIO_IMPLAUSIBLE"
    ROC_EXCEEDS_ACB = "ROC_EXCEEDS_ACB"
    INCOME_WITHOUT_POSITION = "INCOME_WITHOUT_POSITION"
    SETTLE_BEFORE_TRADE = "SETTLE_BEFORE_TRADE"
    SETTLE_LAG_OUTLIER = "SETTLE_LAG_OUTLIER"
    AMBIGUOUS_FEE_CONVENTION = "AMBIGUOUS_FEE_CONVENTION"
    FXT_AMOUNT_INCONSISTENT = "FXT_AMOUNT_INCONSISTENT"
    UNKNOWN_ACCOUNT_TYPE = "UNKNOWN_ACCOUNT_TYPE"
    TRANSFER_UNPAIRED = "TRANSFER_UNPAIRED"
    REVERSAL_PAIR = "REVERSAL_PAIR"
    SIGN_RULE_DISAGREEMENT = "SIGN_RULE_DISAGREEMENT"
    SUPERFICIAL_LOSS_SUSPECT = "SUPERFICIAL_LOSS_SUSPECT"


class TransactionContext(StrEnum):
    """Context for transaction display to control column visibility."""

    IMPORT = "import"  # Import context: hide TxnId and SettleDate
    SETTLEMENT = "settlement"  # Settlement context: show all columns including TxnId
    GENERAL = "general"  # General context: show all columns


class Table(StrEnum):
    """Table names."""

    TXNS = "Txns"
    FX = "FX"
    TICKER_ALIASES = "TickerAliases"


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

    class Aliases(StrEnum):
        """Ticker Aliases columns."""

        OLD_TICKER = "OldTicker"
        NEW_TICKER = "NewTicker"
        EFFECTIVE_DATE = "EffectiveDate"


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

ALIASES_COLUMN_DEFINITIONS = [
    ColumnDefinition(
        Column.Aliases.OLD_TICKER,
        "TEXT",
        "PRIMARY KEY",
    ),
    ColumnDefinition(
        Column.Aliases.NEW_TICKER,
        "TEXT",
        "NOT NULL",
    ),
    ColumnDefinition(
        Column.Aliases.EFFECTIVE_DATE,
        "TEXT",
        (
            f'NOT NULL CHECK(length("{Column.Aliases.EFFECTIVE_DATE}") = 10 AND '
            f'"{Column.Aliases.EFFECTIVE_DATE}" GLOB "{DATE_PATTERN_YYYY_MM_DD}")'
        ),
    ),
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
