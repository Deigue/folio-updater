import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
CONFIG = {}
FOLIO_PATH = None
SHEETS = {}

# Default configuration
DEFAULT_CONFIG = {
    "folio_path": "data/folio.xlsx",
    "sheets": {
        "tickers": "Tickers"
    }
}

def load_config(path=CONFIG_PATH):
    """
    Load config.yaml from disk, creating it if missing.
    """
    if not path.exists():
        with open(path, "w") as f:
            yaml.safe_dump(DEFAULT_CONFIG, f)

    # Load config
    with open(path, "r") as f:
        config = yaml.safe_load(f)
    return config

def reload_config():
    """Reload config.yaml and update module-level constants."""
    global CONFIG, FOLIO_PATH, SHEETS
    CONFIG = load_config()
    folio_path = Path(CONFIG["folio_path"])
    if not folio_path.is_absolute():
        folio_path = PROJECT_ROOT / folio_path
    FOLIO_PATH = folio_path.resolve()
    SHEETS = CONFIG.get("sheets", {})

# Initial load
reload_config()
