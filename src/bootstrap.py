"""
Bootstrap the project by ensuring the project root is in sys.path.
This allows importing from src/ anywhere (scripts, notebooks, etc.)
without adjusting PYTHONPATH manually.
"""


import sys
import logging
from pathlib import Path
from src import config
from src.logging_setup import init_logging

LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

project_root = Path(__file__).resolve().parent.parent
# Detect if we've already added the root to avoid duplicates
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root)) # pragma: no cover

def _get_log_level():
    return LEVEL_MAP.get(config.LOG_LEVEL, logging.ERROR)

def _init_logging_from_config():
    init_logging(level=_get_log_level())

def reload_config():
    config.load_config()
    _init_logging_from_config()
    logger.info(f"Loaded config and set log level to: {logging.getLevelName(_get_log_level())}")
    return config

# Bootstrap app...
logger = logging.getLogger(__name__)
reload_config()

# Config validation
if not Path(config.FOLIO_PATH).parent.exists():
    logger.warning(f"Folio directory does not exist: {config.FOLIO_PATH.parent}")  # pragma: no cover
