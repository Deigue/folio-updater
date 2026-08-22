"""Fixed domain values that are not user-configurable."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from domain.enums import Column

TORONTO_TZ = ZoneInfo("America/Toronto")


# Internal transaction fields that are essential for processing
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
