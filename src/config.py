"""
Configuration management for the application.
This module handles loading and managing the application's configuration settings,
"""


import yaml
from pathlib import Path

# Module-level constants for configuration
PROJECT_ROOT = Path(__file__).resolve().parent.parent
def get_config_path():
    return PROJECT_ROOT / "config.yaml"
CONFIG = {}
FOLIO_PATH = None
LOG_LEVEL = None
SHEETS = {}
HEADER_KEYWORDS = {}

# Default configuration
DEFAULT_CONFIG = {
    "folio_path": "data/folio.xlsx",
    "log_level": "ERROR",
    "sheets": {
        "tickers": "Tickers"
    },
    "header_keywords": {
        "TxnDate": ["txndate", "transaction date", "date"],
        "Action": ["action", "type", "activity"],
        "Amount": ["amount", "value", "total"],
        "$": ["$", "currency", "curr"],
        "Price": ["price", "unit price", "share price"],
        "Units": ["units", "shares", "qty", "quantity"],
        "Ticker": ["ticker", "symbol", "stock"]
    }
}

def load_config():
    global CONFIG, FOLIO_PATH, LOG_LEVEL, SHEETS, HEADER_KEYWORDS
    """
    Load config.yaml from disk, creating it if missing.
    """
    path = get_config_path()
    if not path.exists():
        with open(path, "w") as f:
            yaml.safe_dump(DEFAULT_CONFIG, f)

    # Load config
    with open(path, "r") as f:
        config = yaml.safe_load(f)

    CONFIG = config
    folio_path = Path(CONFIG["folio_path"])
    if not folio_path.is_absolute():
        folio_path = PROJECT_ROOT / folio_path
    FOLIO_PATH = folio_path.resolve()
    LOG_LEVEL = CONFIG.get("log_level", "ERROR").upper()
    SHEETS = CONFIG.get("sheets", {})
    HEADER_KEYWORDS = CONFIG.get("header_keywords", {})

    return config
