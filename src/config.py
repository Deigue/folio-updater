import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
CONFIG = {}
SHEET_NAME = None

# Default configuration
DEFAULT_CONFIG = {
    "sheet_name": "Tickers",
}

def load_config(path=CONFIG_PATH):
    # Created config.yaml if it doesn't exist
    if not CONFIG_PATH.exists():
        with open(path, "w") as f:
            yaml.safe_dump(DEFAULT_CONFIG, f)

    # Load config
    with open(path, "r") as f:
        config = yaml.safe_load(f)
    return config

def reload_config():
    """Reload config.yaml and update module-level constants."""
    global CONFIG, SHEET_NAME
    CONFIG = load_config()
    SHEET_NAME = CONFIG["sheet_name"]

def get_config_value(key, default=None):
    """Get a configuration value by key, with an optional default."""
    return CONFIG.get(key, default)

# Initial load
reload_config()
