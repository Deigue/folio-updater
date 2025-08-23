"""Bootstrap module."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from src import config
from src.logging_setup import init_logging

if TYPE_CHECKING:
    from types import ModuleType

LEVEL_MAP: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

project_root = Path(__file__).resolve().parent.parents
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))  # pragma: no cover


def _get_log_level() -> int:
    return LEVEL_MAP.get(config.LOG_LEVEL, logging.ERROR)


def _init_logging_from_config() -> None:
    init_logging(level=_get_log_level())


def reload_config() -> ModuleType:
    """Reload the configuration from disk, updating log levels as needed.

    Returns:
        ModuleType: The loaded configuration module.

    """
    config.load_config()
    _init_logging_from_config()
    logger.info(
        "Loaded config and set log level to: %s",
        logging.getLevelName(_get_log_level()),
    )
    return config


# Bootstrap app...
logger: logging.Logger = logging.getLogger(__name__)
reload_config()

# Config validation
if config.FOLIO_PATH is not None and not Path(config.FOLIO_PATH).parent.exists():
    logger.warning(
        "Folio directory does not exist: %s",
        Path(config.FOLIO_PATH).parent,
    )  # pragma: no cover
