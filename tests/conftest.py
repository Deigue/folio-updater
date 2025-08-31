"""Pytest configuration and fixtures for folio-updater tests."""

from __future__ import annotations

import logging
from contextlib import _GeneratorContextManager, contextmanager
from pathlib import Path
from typing import Any, Callable, Generator

import pytest

from app.app_context import AppContext


@pytest.fixture(autouse=True)
def reset_app_context() -> None:
    """Automatically reset AppContext before each test."""
    AppContext.reset_singleton()


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
            overrides = {}

        config_path: Path = tmp_path / "config.yaml"
        with Path.open(config_path, "w") as f:
            for key, value in overrides.items():
                f.write(f"{key}: {value}\n")

        # Get a fresh instance after the reset_app_context fixture has run
        app_ctx = AppContext.get_instance()
        app_ctx.initialize(tmp_path)

        try:
            yield app_ctx
        finally:
            # Additional cleanup - reset the instance config
            config_path.unlink(missing_ok=True)

            # Clean artifacts created by folio_setup/mock_data
            for pattern in ("*.xlsx", "*.db"):
                for file_path in tmp_path.rglob(pattern):
                    file_path.unlink(missing_ok=True)

            _log_cleanup_status(tmp_path)

    return _temp_config


def _log_cleanup_status(tmp_path: Path) -> None:  # pragma: no cover
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
