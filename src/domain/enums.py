"""The vocabulary the whole codebase speaks: actions, currencies, columns.

Pure value types. This module imports nothing of ours, which is what lets
every other layer depend on it.
"""

from __future__ import annotations

from enum import StrEnum


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
    DUPLICATE_SPLIT = "DUPLICATE_SPLIT"
    SPLIT_SCOPE_MISMATCH = "SPLIT_SCOPE_MISMATCH"
    SPLIT_WITHOUT_POSITION = "SPLIT_WITHOUT_POSITION"
    ROC_EXCEEDS_ACB = "ROC_EXCEEDS_ACB"
    INCOME_WITHOUT_POSITION = "INCOME_WITHOUT_POSITION"
    MIXED_CURRENCY = "MIXED_CURRENCY"
    SETTLE_BEFORE_TRADE = "SETTLE_BEFORE_TRADE"
    SETTLE_LAG_OUTLIER = "SETTLE_LAG_OUTLIER"
    AMBIGUOUS_FEE_CONVENTION = "AMBIGUOUS_FEE_CONVENTION"
    FXT_AMOUNT_INCONSISTENT = "FXT_AMOUNT_INCONSISTENT"
    UNKNOWN_ACCOUNT_TYPE = "UNKNOWN_ACCOUNT_TYPE"
    TRANSFER_UNPAIRED = "TRANSFER_UNPAIRED"
    SUPERFICIAL_LOSS_SUSPECT = "SUPERFICIAL_LOSS_SUSPECT"


class SettlementOutcome(StrEnum):
    """How a statement row fared against the folio's calculated settlement dates."""

    MATCHED = "MATCHED"  # Exactly one transaction matched; its date was updated.
    ALREADY_SETTLED = "ALREADY_SETTLED"  # Matched a row that already has its date.
    UNMATCHED = "UNMATCHED"  # Nothing in the folio matched the row.
    AMBIGUOUS = "AMBIGUOUS"  # Several matched, so none could be updated.


class QuoteStatus(StrEnum):
    """How the last attempt to price a symbol came out."""

    OK = "OK"  # A price was returned and stored.
    NOT_FOUND = "NOT_FOUND"  # The provider does not know this symbol.
    ERROR = "ERROR"  # The fetch failed; any price on the row is the previous one.


class CheckStatus(StrEnum):
    """How a `folio check` check came out."""

    OK = "OK"  # Nothing to report.
    WARN = "WARN"  # Probably fine, but worth seeing.
    FAIL = "FAIL"  # Wrong, and the folio should be corrected.


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
    QUOTES = "Quotes"


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

    class Quote(StrEnum):
        """Cached market quote columns."""

        SYMBOL = "Symbol"  # The folio's own canonical symbol, e.g. REI.UN.TO
        YSYMBOL = "YSymbol"  # How the provider spells it, e.g. REI-UN.TO
        PRICE = "Price"
        PREV_CLOSE = "PrevClose"
        CURRENCY = "Currency"
        NAME = "Name"
        SECTOR = "Sector"
        EXCHANGE = "Exchange"
        MARKET_CAP = "MarketCap"
        QUOTE_TIME = "QuoteTime"  # The provider's own market timestamp
        FETCHED_AT = "FetchedAt"  # Drives the price TTL
        META_FETCHED_AT = "MetaFetchedAt"  # Drives the slower metadata TTL
        SOURCE = "Source"
        STATUS = "Status"
