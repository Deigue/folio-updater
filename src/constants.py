"""
Non-user-configurable defaults/constants used throughout the app.
"""

# Internal transaction fields that are essential for processing
TXN_ESSENTIALS = [
    "TxnDate",  # Date of transaction
    "Action",   # BUY/SELL
    "Amount",   # Total amount (Price * Units)
    "$",        # Currency
    "Price",    # Price per unit
    "Units",    # Number of units
    "Ticker"    # Stock or ETF ticker
]

# Default tickers for newly created folio file
DEFAULT_TICKERS = ["SPY", "AAPL"]
