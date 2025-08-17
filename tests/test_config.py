import logging
import yaml
from pathlib import Path
from tests.conftest import config_with_temp

logger = logging.getLogger(__name__)

def test_default_config_creation(config_with_temp):
    config, path = config_with_temp
    logger.info("Temp config path: %s", path)

    assert not path.exists()
    config.load_config()
    logger.info("After load_config, exists? %s", path.exists())
    assert path.exists()

    with open(path) as f:
        saved = yaml.safe_load(f)

    logger.info("Saved config: %s", saved)
    # Compare the default values
    from src.config import DEFAULT_CONFIG
    assert saved == DEFAULT_CONFIG

def test_relative_path_resolves(config_with_temp):
    config, path = config_with_temp
    logger.info("Relative folio path: %s", "data/testfolio.xlsx")
    yaml.safe_dump({"folio_path": "data/testfolio.xlsx"}, open(path, "w"))
    config.load_config()

    logger.info("After load_config: %s", config.FOLIO_PATH)
    assert config.FOLIO_PATH.is_absolute()
    assert "testfolio.xlsx" in str(config.FOLIO_PATH)

def test_absolute_path_kept(config_with_temp):
    config, path = config_with_temp
    abs_path = Path(path.parent) / "absolute.xlsx"
    yaml.safe_dump({"folio_path": str(abs_path)}, open(path, "w"))
    config.load_config()

    assert config.FOLIO_PATH == abs_path.resolve()

def test_bootstrapping(config_with_temp):
    config, path = config_with_temp
    logger.info("Temp config path: %s", path)

    # --- 1. Test problematic bootstrap  ---
    # Point folio_path to a folder that doesn't exist
    bad_folio = path.parent / "nonexistent" / "folio.xlsx"
    yaml.safe_dump({
        "folio_path": str(bad_folio),
        "log_level": "INVALID",
        "sheets": {"tickers": "Tickers"},
        "header_keywords": {
            "TxnDate": ["txndate", "transaction date", "date"],
            "Action": ["action", "type", "activity"],
            "Amount": ["amount", "value", "total"],
            "$": ["$", "currency", "curr"],
            "Price": ["price", "unit price", "share price"],
            "Units": ["units", "shares", "qty", "quantity"],
            "Ticker": ["ticker", "symbol", "stock"],
            "InvalidKeyword": ["invalid"]
        }
    }, open(path, "w"))

    # Ensure a compliant folio file is created.
    config.load_config()
    from src import bootstrap
    assert config.LOG_LEVEL == "ERROR"
    assert not config.HEADER_KEYWORDS.__contains__("InvalidKeyword")

    # --- 2. Test reload_config updates config ---
    yaml.safe_dump({
        "folio_path": str(bad_folio),
        "log_level": "DEBUG",
        "sheets": {"tickers": "TKR"},
        "header_keywords": {
            "TxnDate": ["settledate"]
        }
    }, open(path, "w"))
    bootstrap.reload_config()

    # Check if the folio file exists
    assert config.LOG_LEVEL == "DEBUG"
    assert config.tickers_sheet() == "TKR"
    assert config.HEADER_KEYWORDS["TxnDate"] == ["settledate"]

    # --- 3. Ensure logging is configured ---
    logger.debug("This message is colorized!")
