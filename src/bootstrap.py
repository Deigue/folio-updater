"""
Bootstrap the project by ensuring the project root is in sys.path.
This allows importing from src/ anywhere (scripts, notebooks, etc.)
without adjusting PYTHONPATH manually.
"""

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent

# Detect if we've already added the root to avoid duplicates
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# Load config before logging
from src import config

# Import logger setup
from src.logging_config import setup_logging
import logging

# Map string to logging level
level_map = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}
log_level = level_map.get(config.LOG_LEVEL, logging.ERROR)

# Setup logging with level from config
setup_logging(level=log_level)

logger = logging.getLogger(__name__)
# Config validation
if not Path(config.FOLIO_PATH).parent.exists():
    logger.warning(f"Folio directory does not exist: {config.FOLIO_PATH.parent}")

def reload_config():
    """
    Reload the config.yaml file and reapply logging settings.
    """
    import importlib
    from src import config

    # Reload config.py to refresh values
    importlib.reload(config)

    new_level = level_map.get(config.LOG_LEVEL, logging.ERROR)
    logging.getLogger().setLevel(new_level)
    logger.info(f"Reloaded config and set log level to: {logging.getLevelName(new_level)}")

    return config