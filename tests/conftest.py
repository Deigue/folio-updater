"""Pytest configuration and fixtures for folio-updater tests."""

from __future__ import annotations

import logging
from contextlib import _GeneratorContextManager, contextmanager
from pathlib import Path
from typing import Any, Callable, Generator

import pytest

from app.app_context import AppContext


@pytest.fixture
def temp_config(
    tmp_path: Path,
) -> Callable[..., _GeneratorContextManager[AppContext, None, None]]:
    """Create a temporary project structure with an isolated config.yaml.

    Args:
        tmp_path: A Path object pointing to a temporary directory.

    Yields:
        A function that can be called with keyword arguments to create a Config
        instance with those overrides.

    """

    @contextmanager
    def _temp_config(
        overrides: dict[str, Any] | None = None,
    ) -> Generator[AppContext, Any, None]:
        if overrides is None:
            overrides = {}  # pragma: no cover
        config_path: Path = tmp_path / "config.yaml"
        with Path.open(config_path, "w") as f:
            for key, value in overrides.items():
                f.write(f"{key}: {value}\n")

        app_ctx = AppContext.get_instance()
        app_ctx.initialize(tmp_path)
        yield app_ctx

        app_ctx.reset()
        _log_cleanup_status(tmp_path)
        _close_log_handlers()
        if config_path.exists():
            config_path.unlink()

    return _temp_config


def _close_log_handlers() -> None:
    for handler in logging.root.handlers[:]:
        handler.close()
        logging.root.removeHandler(handler)


def _log_cleanup_status(tmp_path: Path) -> None:
    """Log the cleanup status of the temporary directory."""
    logger = logging.getLogger(__name__)
    if not tmp_path.exists():
        logger.info(
            "\nTemporary directory %s does not exist (already cleaned by pytest).\n",
            tmp_path,
        )
        return

    total_size = 0
    leftover_files = []
    for file_path in tmp_path.rglob("*"):
        if file_path.is_file():
            size = file_path.stat().st_size
            total_size += size
            leftover_files.append((file_path, size))

    if total_size != 0:
        logger.debug(
            "\nTemporary directory %s has leftover files (total size: %d bytes):\n",
            tmp_path,
            total_size,
        )
        for file_path, size in leftover_files:
            logger.warning("  Leftover file: %s (size: %d bytes)\n", file_path, size)
