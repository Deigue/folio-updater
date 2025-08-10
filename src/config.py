import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
CONFIG = {}
FOLIO_PATH = None
LOG_LEVEL = None
SHEETS = {}

# Default configuration
DEFAULT_CONFIG = {
    "folio_path": "data/folio.xlsx",
    "log_level": "ERROR",
    "sheets": {
        "tickers": "Tickers"
    }
}

def load_config(path=CONFIG_PATH):
    global CONFIG, FOLIO_PATH, LOG_LEVEL, SHEETS
    """
    Load config.yaml from disk, creating it if missing.
    """
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

    return config

# Initial load
load_config()
