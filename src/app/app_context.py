"""Application context manager.

This module provides the AppContext class and related functions to manage
application-wide configuration.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from app.logging_setup import init_logging
from config import Config

if TYPE_CHECKING:
    from pathlib import Path

logger: logging.Logger = logging.getLogger(__name__)


class AppContext:
    """The process's one config, loaded on first use and shared by every later read.

    Attributes:
        _instance: The singleton instance of AppContext.
        _lock: Thread lock for safe lazy initialization in multi-threaded environments.
        config: The Config instance, loading it if necessary.
    """

    _instance: AppContext | None = None
    _lock = threading.Lock()
    _allow_reinit: bool = False  # Add this class attribute

    def __init__(self) -> None:
        """Private constructor to prevent direct instantiation."""
        if AppContext._instance is not None and not AppContext._allow_reinit:
            msg = "Use AppContext.get_instance() instead"
            raise RuntimeError(msg)
        self._config: Config | None = None

    @classmethod
    def get_instance(cls) -> AppContext:
        """Get the singleton instance."""
        if cls._instance is None:
            with cls._lock:
                cls._instance = cls()
        return cls._instance

    @property
    def config(self) -> Config:
        """Return the process config, loading the default project's on first use."""
        config = self._config
        if config is None:  # pragma: no cover
            # Two threads reading at once still load, and set up logging, only once.
            with AppContext._lock:
                config = self._config or self.load(None)
        return config

    def initialize(self, project_root: Path | None) -> None:
        """Load a project root's config, without setting up logging."""
        self._config = Config.load(project_root)

    def load(self, project_root: Path | None) -> Config:
        """Load the config from disk, then set up logging from its levels.

        Logging starts only after the load, so loading the config logs nothing.
        """
        self.initialize(project_root)
        config = self.config
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

    @classmethod
    def reset_singleton(cls) -> None:
        """Reset the singleton instance completely (for testing only)."""
        with cls._lock:
            cls._allow_reinit = True
            cls._instance = None
            cls._allow_reinit = False


def initialize_app(project_root: Path | None = None) -> None:
    """Initialize the application."""
    AppContext.get_instance().initialize(project_root)


def reload_config(project_root: Path | None = None) -> Config:
    """Replace the process config with a fresh load from disk, logging included.

    Returns:
        Config: The reloaded configuration object.
    """
    return AppContext.get_instance().load(project_root)


def reset_context() -> None:  # pragma: no cover
    """Reset the global application context (for testing)."""
    AppContext.reset_singleton()


def get_config() -> Config:
    """Get the process config, loading it on first use."""
    return AppContext.get_instance().config
