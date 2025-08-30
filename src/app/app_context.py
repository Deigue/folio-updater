"""Application context manager.

This module provides the AppContext class and related functions to manage
application-wide configuration.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from utils.config import Config

if TYPE_CHECKING:
    from pathlib import Path


class AppContext:
    """AppContext manages the application's configuration through a singleton pattern.

    This class ensures a single, thread-safe instance of the application context,
    providing lazy-loaded access to the configuration.

    Attributes:
        _instance: The singleton instance of AppContext.
        _lock: Thread lock for safe lazy initialization in multi-threaded environments.

    Properties:
        config: Returns the Config instance, loading it if necessary.

    Methods:
        __init__: Private constructor to prevent direct instantiation.
        get_instance: Class method to get the singleton instance.
        initialize: Initializes the context with a specific project root.
        reset: Resets the context for testing by clearing cached config.
    """

    _instance: AppContext | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        """Private constructor to prevent direct instantiation."""
        if AppContext._instance is not None:  # pragma: no cover
            msg = "Use AppContext.get_instance() instead"
            raise RuntimeError(msg)
        self._config: Config | None = None

    @classmethod
    def get_instance(cls) -> AppContext:
        """Get the singleton instance."""
        if cls._instance is None:
            with cls._lock:
                cls._instance = cls()  # pragma: no cover
        return cls._instance

    @property
    def config(self) -> Config:
        """Return the Config instance directly."""
        if self._config is None:
            self._config = Config.load()  # pragma: no cover
        return self._config

    def initialize(self, project_root: Path | None) -> None:
        """Initialize the application context with config."""
        self._config = Config.load(project_root)

    def reset(self) -> None:
        """Reset the context for testing (clears cached instances)."""
        self._config = None


def initialize_app(project_root: Path | None = None) -> None:
    """Initialize the application."""
    AppContext.get_instance().initialize(project_root)


def reset_context() -> None:  # pragma: no cover
    """Reset the global application context (for testing)."""
    AppContext.get_instance().reset()


def get_config() -> Config:
    """Get the Config instance directly."""
    return AppContext.get_instance().config
