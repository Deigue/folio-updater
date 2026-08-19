"""Terminal theme/formatting constants and functions."""

from __future__ import annotations

import re

from utils import Action, Column

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
}

SHORT_ACTIONS = {
    str(Action.DIVIDEND): "DIV",
    str(Action.SPLIT): "SPL",
    str(Action.CONTRIBUTION): "CON",
    str(Action.WITHDRAWAL): "WDL",
    str(Action.TFR_IN): "TFI",
    str(Action.TFR_OUT): "TFO",
}

ACTION_HEADERS = (str(Column.Txn.ACTION), SHORT_HEADERS[str(Column.Txn.ACTION)])

ROUNDABLE_HEADERS = frozenset({"Price", "Avg", "Avg\nUSD"})
DECIMAL_RUN = re.compile(r"-?[\d,]*\d\.\d+")

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
