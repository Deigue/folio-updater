"""Pytest configuration and fixtures for folio-updater tests."""

from __future__ import annotations

import logging
import shutil
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pandas as pd
import pytest
import yaml

from app import AppContext, get_config
from datagen import create_mock_data, get_mock_data_date_range
from services import ForexService
from utils.constants import TORONTO_TZ, Column, Currency
from utils.settlement_calculator import settlement_calculator

from .fixtures.dataframe_cache import dataframe_cache_patching  # noqa: F401

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from .test_types import TempContext

# Store the original function before any monkey patching
import datagen.folio_setup

_original_ensure_data_exists = datagen.folio_setup.ensure_data_exists

# Global state to track the active cached_mock_data path
_active_cached_mock_data: Path | None = None


def _patched_ensure_data_exists(*, mock: bool = True) -> None:
    """Global patched version of ensure_data_exists that uses cached data."""
    logger = logging.getLogger(__name__)

    if not mock:  # pragma: no cover
        _original_ensure_data_exists(mock=False)
        return

    config = get_config()
    if config.txn_parquet.exists():
        return

    # Include the original validation logic from ensure_data_exists
    folio_path_parent: Path = config.folio_path.parent
    default_data_dir: Path = config.project_root / "data"

    # Only create data folder in automated fashion
    if folio_path_parent.is_relative_to(default_data_dir):
        folio_path_parent.mkdir(parents=True, exist_ok=True)
    elif not folio_path_parent.exists():
        msg: str = f'MISSING folder: "{folio_path_parent}"'
        logger.error(msg)
        raise FileNotFoundError(msg)

    if _active_cached_mock_data is None:  # pragma: no cover
        _original_ensure_data_exists(mock=True)
        return

    config.data_path.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_active_cached_mock_data / "folio.db", config.db_path)
    shutil.copy2(_active_cached_mock_data / "transactions.parquet", config.txn_parquet)
    shutil.copy2(_active_cached_mock_data / "tickers.parquet", config.tkr_parquet)
    fx_src = _active_cached_mock_data / "fx.parquet"
    if fx_src.exists():  # pragma: no cover
        shutil.copy2(fx_src, config.fx_parquet)


# Replace the function at module level so all imports get the patched version
datagen.folio_setup.ensure_data_exists = _patched_ensure_data_exists

# Session-scoped caches
_fx_cache: dict[str, pd.DataFrame] = {}
_mock_data_cache: dict[str, Path] = {}


@pytest.fixture(autouse=True)
def reset_app_context() -> None:
    """Automatically reset AppContext before each test."""
    AppContext.reset_singleton()


@pytest.fixture(autouse=True)
def activate_dataframe_cache(
    dataframe_cache_patching: None,  # noqa: F811
) -> None:
    """Globally activate DataFrame cache patching for all tests."""
    # The fixture is only needed for activation


@pytest.fixture
def temp_ctx(tmp_path: Path) -> TempContext:
    """Create a temporary project structure with an isolated config.yaml.

    Args:
        tmp_path: A Path object pointing to a temporary directory.

    Yields:
        A function that can be called with keyword arguments to create a Config
        instance with those overrides.

    """

    @contextmanager
    def _temp_ctx(
        overrides: dict[str, Any] | None = None,
        **kwargs: str | list[str] | dict[str, Any],
    ) -> Generator[AppContext, Any]:
        if overrides is None:
            overrides = {}

        # Convert mappingproxy to dict if needed and merge kwargs into overrides
        overrides = dict(overrides)
        overrides.update(kwargs)

        config_path: Path = tmp_path / "config.yaml"
        if overrides:
            with Path.open(config_path, "w") as f:
                yaml.safe_dump(overrides, f, default_flow_style=False)

        # Get a fresh instance after the reset_app_context fixture has run
        app_ctx = AppContext.get_instance()
        app_ctx.initialize(tmp_path)

        try:
            yield app_ctx
        finally:
            # Additional cleanup - reset the instance config
            config_path.unlink(missing_ok=True)

            # Clean artifacts created by folio_setup/mock_data
            for pattern in ("*.xlsx", "*.db", "*.parquet", "*.csv"):
                for file_path in tmp_path.rglob(pattern):
                    file_path.unlink(missing_ok=True)

            _log_cleanup_status(tmp_path)

    return _temp_ctx


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
            logger.warning("  LEFTOVER FILE: %s (size: %d bytes)\n", file_path, size)


@pytest.fixture(scope="session")
def cached_fx_data() -> Callable[[str | None], pd.DataFrame]:
    """Fixture returning a function to fetch and slice cached FX data."""
    cache_key = "fx_data_60days"
    if cache_key not in _fx_cache:
        default_start = (datetime.now(TORONTO_TZ) - timedelta(days=60)).strftime(
            "%Y-%m-%d",
        )
        forex_service = ForexService()
        _fx_cache[cache_key] = forex_service.get_fx_rates_from_boc(default_start)

    def get_fx_data(start_date: str | None = None) -> pd.DataFrame:
        df = _fx_cache[cache_key].copy()
        if start_date is not None:
            df = df[df[Column.FX.DATE] >= start_date]
        return df

    return get_fx_data


@pytest.fixture(scope="session")
def cached_mock_data(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create and cache mock data files for the entire test session.

    This fixture generates mock data (parquet files and sqlite db) once per session
    and stores them in a temporary directory that can be copied to
    individual test contexts. This directory persists across individual tests.

    Returns:
        Path to the cached data directory containing generated mock files.
    """
    logger = logging.getLogger(__name__)
    cache_dir = tmp_path_factory.mktemp("mock_data_cache")

    logger.debug("Generating cached mock data at %s", cache_dir)

    # Store paths for reference
    _mock_data_cache["root"] = cache_dir
    _mock_data_cache["db_path"] = cache_dir / "folio.db"
    _mock_data_cache["txn_parquet"] = cache_dir / "transactions.parquet"
    _mock_data_cache["tkr_parquet"] = cache_dir / "tickers.parquet"
    _mock_data_cache["fx_parquet"] = cache_dir / "fx.parquet"

    # Build a small mock FX frame so the creation routine doesn't reach
    # out to any external services during cache generation.
    mock_fx_data = pd.DataFrame(
        {
            Column.FX.DATE: ["2022-01-01"],
            Column.FX.FXUSDCAD: [1.25],
            Column.FX.FXCADUSD: [0.8],
        },
    )

    # Ensure a fresh AppContext for generation
    AppContext.reset_singleton()
    app_ctx = AppContext.get_instance()
    app_ctx.initialize(cache_dir)

    with (
        patch.object(ForexService, "get_missing_fx_data", return_value=mock_fx_data),
        patch.object(ForexService, "insert_fx_data", return_value=None),
    ):
        create_mock_data()

    AppContext.reset_singleton()
    logger.debug("Cached mock data generated successfully")
    return cache_dir / "data"


@pytest.fixture(autouse=True)
def use_cached_mock_data(
    cached_mock_data: Path,
) -> Generator[None, Any]:
    """Set the global cached mock data path for the patched ensure_data_exists."""
    global _active_cached_mock_data  # noqa: PLW0603

    old_path = _active_cached_mock_data
    _active_cached_mock_data = cached_mock_data
    try:
        yield
    finally:
        _active_cached_mock_data = old_path


@pytest.fixture(scope="session", autouse=True)
def preload_settlement_schedules() -> None:
    """Pre-load settlement calculator schedules once for the entire test suite.

    This fixture runs once per test session and caches market calendar schedules
    to minimize pandas_market_calendars API interactions during tests.
    """
    logger = logging.getLogger(__name__)
    start_date, end_date = get_mock_data_date_range()
    buffer_start = start_date - pd.Timedelta(days=10)
    buffer_end = end_date + pd.Timedelta(days=30)

    logger.debug(
        "PRE-LOADING market calendars for testing from %s to %s",
        buffer_start.date(),
        buffer_end.date(),
    )

    # Pre-load schedules into instance cache
    settlement_calculator.calendar_schedules[Currency.USD] = (
        settlement_calculator.get_calendar_schedule(
            Currency.USD,
            buffer_start,
            buffer_end,
        )
    )
    settlement_calculator.calendar_schedules[Currency.CAD] = (
        settlement_calculator.get_calendar_schedule(
            Currency.CAD,
            buffer_start,
            buffer_end,
        )
    )


@pytest.fixture(scope="session", autouse=True)
def cleanup_mock_data_cache(cached_mock_data: Path) -> Generator[None, Any]:  # noqa: ARG001
    """Cleanup the mock_data_cache directory after the test session."""
    cache_root = _mock_data_cache.get("root")
    yield

    if cache_root is not None and cache_root.exists():
        shutil.rmtree(cache_root, ignore_errors=True)
        logging.getLogger(__name__).debug(
            "Cleaned up mock_data_cache at %s",
            cache_root,
        )
