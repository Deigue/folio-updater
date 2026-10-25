"""Fixed domain values that are not user-configurable."""

from __future__ import annotations

from zoneinfo import ZoneInfo

from domain.enums import Column

TORONTO_TZ = ZoneInfo("America/Toronto")

# Weekdays in a week: Monday to Friday, the days a market is open.
WEEKDAYS = 5


# Internal transaction fields that are essential for processing. A tuple, so no
# caller can reorder it and silently change the duplicate key for every import.
TXN_ESSENTIALS: tuple[str, ...] = (
    Column.Txn.TXN_DATE,  # Trade date
    Column.Txn.ACTION,  # Action
    Column.Txn.AMOUNT,  # Total amount (Price * Units)
    Column.Txn.CURRENCY,  # Currency
    Column.Txn.PRICE,  # Price per unit
    Column.Txn.UNITS,  # Number of units
    Column.Txn.TICKER,  # Security ticker, such as a stock or ETF
    Column.Txn.ACCOUNT,  # Account name
)

# Stored transaction fields that exist for the database alone, so a file meant
# to be imported again leaves them out.
INTERNAL_COLUMNS: tuple[str, ...] = (Column.Txn.TXN_ID,)
