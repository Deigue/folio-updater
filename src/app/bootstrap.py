"""Bootstrap module."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from utils.config import Config
from utils.logging_setup import init_logging

if TYPE_CHECKING:
    from pathlib import Path

logger: logging.Logger = logging.getLogger(__name__)


def reload_config(project_root: Path | None = None) -> Config:
    """Reload the configuration from disk, updating log levels as needed.

    Returns:
        Config: The reloaded configuration object.

    """
    config: Config = Config.load(project_root)
    level_int: int = getattr(logging, config.log_level.upper())
    init_logging(level_int)

    logger.info("Loaded config and set log level to: %s", config.log_level)
    if config.folio_path is not None and not config.folio_path.parent.exists():
        logger.warning(
            "Folio directory does not exist: %s",
            config.folio_path.parent,
        )  # pragma: no cover
    return config
