"""
Configuration management for the application.

This module handles loading and managing the application's configuration settings.
It provides a centralized way to access configuration values throughout the application.
"""


import yaml
from pathlib import Path
from typing import Any, Dict, Optional, TypedDict
from copy import deepcopy


# Type definitions
class SheetsConfig(TypedDict):
    tickers: str

HeaderKeywords = TypedDict(
    "HeaderKeywords",
    {
        "TxnDate": list[str],
        "Action": list[str],
        "Amount": list[str],
        "$": list[str],
        "Price": list[str],
        "Units": list[str],
        "Ticker": list[str],
    },
)

class Config(TypedDict):
    folio_path: str
    log_level: str
    sheets: SheetsConfig
    header_keywords: HeaderKeywords

# Module-level constants for configuration
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# Default configuration
DEFAULT_CONFIG: Config = {
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

# Global configuration variables
CONFIG: Config = deepcopy(DEFAULT_CONFIG)
FOLIO_PATH: Optional[Path] = None
LOG_LEVEL: str = DEFAULT_CONFIG["log_level"]
SHEETS: SheetsConfig = DEFAULT_CONFIG["sheets"].copy()
HEADER_KEYWORDS: HeaderKeywords = DEFAULT_CONFIG["header_keywords"].copy()

def _get_config_path() -> Path:
    """
    Get the absolute path to the configuration file.

    Returns:
        Path: Absolute path to the config.yaml file
    """
    return PROJECT_ROOT / "config.yaml"

def _validate_config(config: Dict[str, Any]) -> Config:
    """
    Validate the loaded configuration against expected structure and values.

    Args:
        config: Raw configuration dictionary to validate

    Returns:
        Validated configuration
    """
    validated = deepcopy(DEFAULT_CONFIG)

    # Validate and update log level
    log_level = config.get("log_level", DEFAULT_CONFIG["log_level"]).upper()
    if log_level not in VALID_LOG_LEVELS:
        log_level = DEFAULT_CONFIG["log_level"]
    validated["log_level"] = log_level

    # Update other top-level keys
    if "folio_path" in config:
        validated["folio_path"] = str(config["folio_path"])

    if "sheets" in config and isinstance(config["sheets"], dict):
        validated["sheets"].update(config["sheets"])

    if "header_keywords" in config and isinstance(config["header_keywords"], dict):
        validated["header_keywords"].update({
            k: v for k, v in config["header_keywords"].items()
            if k in DEFAULT_CONFIG["header_keywords"]
        })

    return validated

def load_config() -> Config:
    """
    Load config.yaml from disk, creating it if it doesn't exist.

    Returns:
        Config: The loaded configuration
    """
    global CONFIG, FOLIO_PATH, LOG_LEVEL, SHEETS, HEADER_KEYWORDS
    path = _get_config_path()
    config: Dict[str, Any] = {}

    # Create default config if it doesn't exist
    if not path.exists():
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(DEFAULT_CONFIG, f, default_flow_style=False, sort_keys=False)
    else:
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

    # Validate and update global config
    CONFIG = _validate_config(config)
    folio_path = Path(CONFIG["folio_path"])
    FOLIO_PATH = (PROJECT_ROOT / folio_path).resolve() if not folio_path.is_absolute() else folio_path
    LOG_LEVEL = CONFIG["log_level"]
    SHEETS = CONFIG["sheets"]
    HEADER_KEYWORDS = CONFIG["header_keywords"]
    return CONFIG
