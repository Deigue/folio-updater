"""Pytest configuration and fixtures for folio-updater tests."""

from __future__ import annotations

import logging
import shutil
import sys
from contextlib import _GeneratorContextManager, contextmanager
from pathlib import Path
from typing import Any, Callable, Generator

import pytest
from src.config import Config


def _close_log_handlers() -> None:
    for handler in logging.root.handlers[:]:
        handler.close()
        logging.root.removeHandler(handler)


@pytest.fixture
def temp_config(
    tmp_path: Path,
) -> Callable[..., _GeneratorContextManager[Config, None, None]]:
    """Create a temporary project structure with an isolated config.yaml.

    Args:
        tmp_path: A Path object pointing to a temporary directory.

    Yields:
        A function that can be called with keyword arguments to create a Config
        instance with those overrides.

    """

    @contextmanager
    def _temp_config(**overrides: dict[str, Any]) -> Generator[Config, Any, None]:
        config_path = tmp_path / "config.yaml"
        with Path.open(config_path, "w") as f:
            for key, value in overrides.items():
                f.write(f"{key}: {value}\n")
        yield Config.load(tmp_path)
        _close_log_handlers()
        shutil.rmtree(tmp_path, ignore_errors=True)
        if str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))  # pragma: no cover

    return _temp_config
