import yaml
from pathlib import Path
import logging

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

    # --- 1. Test warning path ---
    # Point folio_path to a folder that doesn't exist
    bad_folio = path.parent / "nonexistent" / "folio.xlsx"
    yaml.safe_dump({
        "folio_path": str(bad_folio),
        "log_level": "DEBUG",
        "sheets": {"tickers": "Tickers"}
    }, open(path, "w"))

    # Ensure the folio file is created
    config.load_config()
    from src import bootstrap
    assert config.LOG_LEVEL == "DEBUG"

    # --- 2. Test reload_config updates config ---
    # Modify log_level in config.yaml
    yaml.safe_dump({
        "folio_path": str(bad_folio),
        "log_level": "CRITICAL",
        "sheets": {"tickers": "Tickers"}
    }, open(path, "w"))

    bootstrap.reload_config()

    # Check if the folio file exists
    assert config.LOG_LEVEL == "CRITICAL"
