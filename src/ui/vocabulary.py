"""Terminal rendering vocabulary: colours, short forms, precisions, rounding."""

from __future__ import annotations

import re
from decimal import Decimal

from domain import AccountType, Action, Column, Currency

# --- Panel border themes -----------------------------------------------------

THEME_MERGED = "bright_blue"  # Merged panels - informational
THEME_TRANSFORMS = "medium_purple3"  # Transforms - modification
THEME_EXCLUDED = "dark_red"  # Excluded/rejected - removal
THEME_DUPES = "dark_red"  # Duplicates - removal
THEME_SUCCESS = "green4"  # Import summary, imported - success

TRANSACTION_COLORS = {
    Action.BUY: "bright_red",
    Action.SELL: "bright_green",
    Action.DIVIDEND: "bright_blue",
    Action.FXT: "cyan",
    Action.FCH: "yellow",
    Action.CONTRIBUTION: "green",
    Action.WITHDRAWAL: "red",
    Action.ROC: "magenta",
    Action.SPLIT: "purple",
    Action.TFR_IN: "green",
    Action.TFR_OUT: "red",
}

# --- Dashboard palette ------------------------------------------------------

# Sign colours
GAIN = "green"
LOSS = "red"
GAIN_STRONG = "bold bright_green"
LOSS_STRONG = "bold bright_red"
FLAT = "dim"

# Ratios (not percentages) at which a figure turns strong or flat.
DAY_MOVE_STRONG_AT = Decimal("0.03")  # Change% and PnL%
RETURN_STRONG_AT = Decimal("0.25")  # Unreal% and Total%
FLAT_BELOW = Decimal("0.001")

# Concentration: a position's weight in its pool or folio.
WEIGHT_WARN_AT = Decimal("0.20")
WEIGHT_ALERT_AT = Decimal("0.30")
WEIGHT_WARN = "yellow"
WEIGHT_ALERT = "bold yellow"
WEIGHT_BAR_STYLE = "dim"
WEIGHT_BAR_WIDTH = 8
WEIGHT_BAR_FULL = Decimal("0.40")  # the weight that fills the bar

INCOME = TRANSACTION_COLORS[Action.DIVIDEND]
REFERENCE_STYLE = "dim"
SUBTOTAL_ROW_STYLE = "bold"
GRAND_TOTAL_ROW_STYLE = "bold bright_white"

CURRENCY_COLORS = {
    Currency.CAD: "medium_purple1",
    Currency.USD: "dark_turquoise",
}

# Flows panel
FLOW_COLORS = {
    "Contributions": TRANSACTION_COLORS[Action.CONTRIBUTION],
    "Withdrawn": TRANSACTION_COLORS[Action.WITHDRAWAL],
    "Net Deposited": "bold bright_white",
    "Dividends": INCOME,
    "Fees": "indian_red",
}

# Contribution room: under-used, partly used, exactly full, over the limit.
ROOM_LOW_BELOW = Decimal("0.5")
ROOM_LOW = "dark_orange"
ROOM_PARTIAL = "yellow"
ROOM_FULL = "green"
ROOM_OVER = "bold bright_red"
ROOM_BAR = ("▰", "▱")  # filled, empty
ROOM_BAR_ASCII = ("#", "-")

ACCOUNT_TYPE_COLORS = {
    AccountType.TFSA: "sea_green2",
    AccountType.FHSA: "aquamarine1",
    AccountType.RRSP: "sky_blue1",
    AccountType.RRIF: "steel_blue1",
    AccountType.LIRA: "light_steel_blue",
    AccountType.RESP: "plum2",
    AccountType.NON_REGISTERED: "light_coral",
    AccountType.MARGIN: "salmon1",
    AccountType.CORPORATE: "tan",
}

# --- Number precision --------------------------------------------------------

MONEY_PRECISION = 2
PRICE_PRECISION = 4
UNIT_PRECISION = 6

# --- Table padding -----------------------------------------------------------

# Cell padding a table falls back to when it will not fit
# (top, right, bottom, left).
SNUG_PADDING = (0, 1, 0, 0)
TIGHT_PADDING = (0, 0)

# --- Shorthands ----------------------------------------------------

SHORT_HEADERS = {
    "TxnId": "Id",
    "Action": "Act",
    "Amount": "Amt",
    "Units": "Qt.",
    "Ticker": "Tkr",
    "Symbol": "Tkr",
    "Account": "Acct",
    "Description": "Desc.",
    "Currency": "$",
    "Transactions": "Txns",
    "Settle Updates": "Settles",
    "Transfers": "Txfs",
    "Rejected": "Rej.",
    "Rejection_Reason": "Reason",
    "OldTicker": "Old",
    "NewTicker": "New",
    "EffectiveDate": "Date",
    "Change": "Chg",
    "Change%": "Chg%",
    "Realized": "Rlzd",
    "Unreal": "Unrl",
    "Unreal%": "Unrl%",
    "Market": "Mkt",
    "Dividends": "Divs",
}

# Percentages that are candidates to drop decimals.
COARSE_PERCENT_HEADERS = frozenset(
    {
        "Unreal%",
        "Unrl%",
        "Total%",
        "Wt%",
        "Folio%",
    },
)
# `Change%` and `PnL%` are excluded as their precision is important.
PERCENT_RUN = re.compile(r"-?[\d,]*\d\.\d+%")

SHORT_ACTIONS = {
    str(Action.DIVIDEND): "DIV",
    str(Action.SPLIT): "SPL",
    str(Action.CONTRIBUTION): "CON",
    str(Action.WITHDRAWAL): "WDL",
    str(Action.TFR_IN): "TFI",
    str(Action.TFR_OUT): "TFO",
}

ACTION_HEADERS = (str(Column.Txn.ACTION), SHORT_HEADERS[str(Column.Txn.ACTION)])


def with_short_forms(headers: frozenset[str]) -> frozenset[str]:
    """Headers with their respective short forms, if any."""
    short = {SHORT_HEADERS[name] for name in headers if name in SHORT_HEADERS}
    return frozenset(headers | short)


ROUNDABLE_HEADERS = with_short_forms(
    frozenset({"Price", "Avg", "Avg\nUSD", "Last", "Change"}),
)
DECIMAL_RUN = re.compile(r"-?[\d,]*\d\.\d+")

# As above, detects decimals but excludes percentages.
MONEY_DECIMAL_RUN = re.compile(r"-?[\d,]*\d\.\d+(?!\d*%)")

# A whole number, used only by the magnitude rung, which runs after cents have
# already gone. Anything touching a `%` or a `.` is left alone.
INTEGER_RUN = re.compile(r"(?<![\d.,])-?\d[\d,]*(?![\d.,%])")

# Columns the cent-dropping and magnitude rungs leave alone...
CENTLESS_EXEMPT_HEADERS = with_short_forms(
    frozenset(
        {
            "Change",
            "Avg",
            "Last",
            "Price",
            "Units",
            "Symbol",
            "Name",
            # Identifiers and dates are exact by nature: never rounded or scaled.
            "TxnId",
            "TxnDate",
            "SettleDate",
            "Settle",
            "Date",
            "EffectiveDate",
        },
    ),
)

# Thresholds the magnitude rung abbreviates at, largest first.
MAGNITUDES: tuple[tuple[float, str], ...] = (
    (1_000_000_000, "B"),
    (1_000_000, "M"),
    (1_000, "K"),
)

CURRENCY_HEADERS = frozenset({str(Column.Txn.CURRENCY), "Currency", "$"})
CURRENCY_HOSTS = frozenset(
    {
        str(Column.Txn.AMOUNT),
        SHORT_HEADERS[str(Column.Txn.AMOUNT)],
        str(Column.Txn.PRICE),
    },
)

# Account shortening
ACCOUNT_WIDTH = 9
ACCOUNT_MIN_WORD = 2
ACCOUNT_HEADERS = (str(Column.Txn.ACCOUNT), SHORT_HEADERS[str(Column.Txn.ACCOUNT)])
ACCOUNT_SEPARATORS = re.compile(r"([-_ /.])")

DOWNLOAD_DROP_ORDER = ("Currency",)
