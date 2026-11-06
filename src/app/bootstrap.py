"""Bootstrap module."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.app_context import get_config, initialize_app
from app.logging_setup import init_logging

if TYPE_CHECKING:
    from pathlib import Path

    from config import Config

logger: logging.Logger = logging.getLogger(__name__)


def reload_config(project_root: Path | None = None) -> Config:
    """Reload the configuration from disk, updating log levels as needed.

    Returns:
        Config: The reloaded configuration object.

    """
    initialize_app(project_root)
    config: Config = get_config()
    levels = logging.getLevelNamesMapping()
    init_logging(
        config.project_root / "logs",
        levels[config.log_level],
        levels[config.audit_log_level],
    )
    logger.info(
        "INIT config (log level: %s, audit log level: %s)",
        config.log_level,
        config.audit_log_level,
    )
    if config.folio_path is not None and not config.folio_path.parent.exists():
        logger.warning(
            "Folio directory does not exist: %s",
            config.folio_path.parent,
        )  # pragma: no cover
    return config
