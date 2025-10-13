"""Pytest configuration and fixtures for folio-updater tests."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Generator
from unittest.mock import patch

import pandas as pd
import pytest
import yaml

from app.app_context import AppContext
from mock.mock_data import get_mock_data_date_range
from services.forex_service import ForexService
from utils.constants import TORONTO_TZ, Column, Currency
from utils.settlement_calculator import settlement_calculator

from .fixtures.dataframe_cache import dataframe_cache_patching  # noqa: F401

if TYPE_CHECKING:
    from .test_types import TempContext

# Shared cache for real FX API data (fetched once per test session)
_fx_cache: dict[str, pd.DataFrame] = {}


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
    ) -> Generator[AppContext, Any, None]:
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


@pytest.fixture(autouse=True)
def mock_forex_data(request: pytest.FixtureRequest) -> Generator[None, Any, None]:
    """Mock ForexService expensive API/database calls for most tests."""
    if request.node.get_closest_marker("no_mock_forex"):
        # Do not mock for this test or module
        yield
        return

    mock_fx_data = pd.DataFrame(
        {
            Column.FX.DATE: ["2022-01-01"],
            Column.FX.FXUSDCAD: [1.25],
            Column.FX.FXCADUSD: [0.8],
        },
    )

    with patch.object(
        ForexService,
        "get_missing_fx_data",
        return_value=mock_fx_data,
    ), patch.object(ForexService, "insert_fx_data", return_value=None):
        yield


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
