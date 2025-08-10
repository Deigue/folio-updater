import yaml
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

def load_config(path=CONFIG_PATH):
    with open(path, "r") as config_file:
        config = yaml.safe_load(config_file)
    return config

# Load once and reuse
CONFIG = load_config()
SHEET_NAME = CONFIG["sheet_name"]

def get_config_value(key, default=None):
    """Get a configuration value by key, with an optional default."""
    return CONFIG.get(key, default)
