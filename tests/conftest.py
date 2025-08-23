"""Pytest configuration and fixtures for folio-updater tests."""

from __future__ import annotations

import logging
import shutil
import sys
from typing import TYPE_CHECKING, Generator

import pytest

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType


def _close_log_handlers() -> None:
    for handler in logging.root.handlers[:]:
        handler.close()
        logging.root.removeHandler(handler)


@pytest.fixture
def config_with_temp(tmp_path: Path) -> Generator[tuple[ModuleType, Path], None, None]:
    """Create a temporary project structure with an isolated config.yaml.

    Args:
        tmp_path: A Path object pointing to a temporary directory.

    Yields:
        A tuple of config.ModuleType and Path, where the first element is the
        config module and the second element is the Path to the temporary
        config.yaml file.

    """
    project_root = tmp_path
    (project_root / "src").mkdir()
    (project_root / "data").mkdir()

    temp_config_path = project_root / "config.yaml"

    """
    Reloads src.config fresh and patches CONFIG_PATH + PROJECT_ROOT
    to use the temporary config path for isolation in tests.
    """
    sys.modules.pop("src.config", None)
    from src import config  # noqa: PLC0415

    config.PROJECT_ROOT = temp_config_path.parent

    yield config, temp_config_path
    _close_log_handlers()  # <-- close before deleting temp dir
    shutil.rmtree(project_root, ignore_errors=True)
    if str(project_root) in sys.path:
        sys.path.remove(str(project_root))  # pragma: no cover
