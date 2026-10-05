"""Pytest configuration and fixtures for folio-updater tests."""

from __future__ import annotations

import logging
import shutil
import sqlite3
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import yaml

from app import AppContext, get_config
from datagen import DemoFolio
from datagen.demo import END, START
from domain import TORONTO_TZ, Column, Currency
from engine.settlement import settlement_calculator
from services import ForexService
from services.quotes_service import QuotesService

from .fixtures.acb_cache import memory_acb_cache  # noqa: F401
from .fixtures.dataframe_cache import dataframe_cache_patching  # noqa: F401
from .helpers.seed import reset_seed_state

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from .test_types import TempContext

# Session-scoped caches
_fx_cache: dict[str, pd.DataFrame] = {}

# Synthetic rates reach this far back from today, or back to the demo's start.
FX_WINDOW_DAYS = 60


@pytest.fixture(scope="session", autouse=True)
def nondurable_sqlite() -> Generator[None, Any]:
    """Drop SQLite's crash-durability for testing scope.

    Test databases live under tmp_path and are deleted the moment the test ends
    """
    real_connect = sqlite3.connect

    def connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:  # noqa: ANN401
        conn = real_connect(*args, **kwargs)
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA journal_mode=MEMORY")
        return conn

    with patch.object(sqlite3, "connect", connect):
        yield


@pytest.fixture(autouse=True)
def reset_app_context() -> None:
    """Automatically reset AppContext before each test."""
    AppContext.reset_singleton()
    reset_seed_state()


@pytest.fixture(autouse=True)
def activate_dataframe_cache(
    dataframe_cache_patching: None,  # noqa: F811
) -> None:
    """Globally activate DataFrame cache patching for all tests."""
    # The fixture is only needed for activation


# Command modules that re-export Parquet after mutating the folio. They bind
# the helper by value (`from cli.commands.common import export_to_parquet`), so
# the stub has to be installed in each importing namespace.
_PARQUET_EXPORT_CALLERS = (
    "cli.commands.add",
    "cli.commands.delete",
    "cli.commands.edit",
    "cli.commands.import_data",
)


@pytest.fixture(autouse=True)
def skip_parquet_export(request: pytest.FixtureRequest) -> Generator[None, Any]:
    """Stub out the Parquet re-export that follows every folio mutation.

    Rewriting the whole transactions table through fastparquet costs ~14ms and
    runs after every add, edit, delete and import: the single most expensive
    step in each. The export itself is covered directly by
    test_transaction_exporter.py, and the tests that assert a mutation refreshes
    the file carry the `real_parquet_export` marker to opt back in.
    """
    if request.node.get_closest_marker("real_parquet_export"):
        yield
        return

    with ExitStack() as stack:
        for module in _PARQUET_EXPORT_CALLERS:
            stack.enter_context(patch(f"{module}.export_to_parquet"))
        yield


@pytest.fixture(autouse=True)
def mock_forex_data(
    request: pytest.FixtureRequest,
    cached_fx_data: Callable[[str | None], pd.DataFrame],
) -> Generator[None, Any]:
    """Keep the test suite off the Bank of Canada API endpoint."""
    if request.node.get_closest_marker("no_mock_forex"):
        yield
        return

    with patch.object(
        ForexService,
        "get_fx_rates_from_boc",
        side_effect=cached_fx_data,
    ):
        yield


@pytest.fixture(scope="session")
def cached_quote_data() -> dict[str, dict[str, Any]]:
    """Build a deterministic stand-in for the quote provider.

    Keyed by the **Yahoo** spelling, since that is what the fetch seams are
    handed. Prices are round numbers so a test can assert an exact market value
    rather than a tolerance, and each symbol's previous close differs from its
    price so the day-move columns have something to show.

    Returns:
        Provider symbol mapped to its raw price and metadata fields.
    """
    return {
        "TESTTKR": {
            "price": 200.0,
            "prev_close": 190.0,
            "currency": "USD",
            "name": "Test Ticker Inc",
            "sector": "Technology",
            "exchange": "NMS",
            "market_cap": 1_000_000_000.0,
        },
        "OTHER": {
            "price": 50.0,
            "prev_close": 55.0,
            "currency": "USD",
            "name": "Other Corp",
            "sector": "Industrials",
            "exchange": "NMS",
            "market_cap": 500_000_000.0,
        },
        "CADCO.TO": {
            "price": 20.0,
            "prev_close": 20.0,
            "currency": "CAD",
            "name": "Canadian Co",
            "sector": "Financials",
            "exchange": "TOR",
            "market_cap": 250_000_000.0,
        },
    }


@pytest.fixture(autouse=True)
def mock_quotes(
    request: pytest.FixtureRequest,
    cached_quote_data: dict[str, dict[str, Any]],
) -> Generator[None, Any]:
    """Keep the test suite off Yahoo Finance.

    Patches the two network seams rather than `yfinance` itself, so a test can
    still assert that the module was never imported.
    """
    if request.node.get_closest_marker("no_mock_quotes"):
        yield
        return

    def prices(ysymbols: list[str]) -> dict[str, dict[str, Any]]:
        return {
            ysymbol: {
                "price": cached_quote_data[ysymbol]["price"],
                "prev_close": cached_quote_data[ysymbol]["prev_close"],
                "currency": cached_quote_data[ysymbol]["currency"],
                "quote_time": datetime.now(UTC).isoformat(),
            }
            for ysymbol in ysymbols
            if ysymbol in cached_quote_data
        }

    def metadata(ysymbols: list[str]) -> dict[str, dict[str, Any]]:
        return {
            ysymbol: {
                key: cached_quote_data[ysymbol][key]
                for key in ("name", "sector", "exchange", "market_cap", "currency")
            }
            for ysymbol in ysymbols
            if ysymbol in cached_quote_data
        }

    with (
        patch.object(QuotesService, "_fetch_prices", side_effect=prices),
        patch.object(QuotesService, "_fetch_metadata", side_effect=metadata),
        patch.object(QuotesService, "_fetch_history", return_value={}),
        patch.object(QuotesService, "_fetch_intraday", return_value={}),
    ):
        yield


@pytest.fixture
def quotes_fetch(
    cached_quote_data: dict[str, dict[str, Any]],
) -> Generator[MagicMock, Any]:
    """Stub the quote fetch the usual way, but hand back the spy.

    For a test that needs to assert *which* symbols were requested, or that
    nothing was requested at all.

    Yields:
        The mock standing in for `QuotesService._fetch_prices`.
    """

    def prices(ysymbols: list[str]) -> dict[str, dict[str, Any]]:
        return {
            ysymbol: {
                "price": cached_quote_data[ysymbol]["price"],
                "prev_close": cached_quote_data[ysymbol]["prev_close"],
                "currency": cached_quote_data[ysymbol]["currency"],
                "quote_time": datetime.now(UTC).isoformat(),
            }
            for ysymbol in ysymbols
            if ysymbol in cached_quote_data
        }

    with (
        patch.object(QuotesService, "_fetch_metadata", return_value={}),
        patch.object(QuotesService, "_fetch_prices", side_effect=prices) as fetch,
    ):
        yield fetch


@pytest.fixture
def boc_fetch(
    cached_fx_data: Callable[[str | None], pd.DataFrame],
) -> Generator[MagicMock, Any]:
    """Stub the BoC fetch the usual way, but hand back the spy.

    Test that needs to assert *which* date was requested, or that
    nothing was requested at all, can do so without patching by hand.

    Yields:
        The mock standing in for `ForexService.get_fx_rates_from_boc`.
    """
    with patch.object(
        ForexService,
        "get_fx_rates_from_boc",
        side_effect=cached_fx_data,
    ) as fetch:
        yield fetch


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

        # Rolling backups are pure disk churn under test: every write command
        # copies the db and rotates old files, and no test asserts on them.
        overrides.setdefault("backup", {"enabled": False})

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

            # Clean artifacts created by the demo folio
            for pattern in ("*.xlsx", "*.db", "*.parquet", "*.csv", "*.meta.json"):
                for file_path in tmp_path.rglob(pattern):
                    file_path.unlink(missing_ok=True)

            # The database this context seeded into has just been deleted, so
            # the next context must build its tables again even though it
            # reuses the same tmp_path.
            reset_seed_state()

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
    """Fixture returning a function to slice a synthetic Bank of Canada FX frame.

    Generated forex dataframe for full offline testing.

    Returns:
        A callable taking an optional inclusive start date (YYYY-MM-DD) and
        returning the matching slice of the cached frame.
    """
    cache_key = "fx_data_60days"
    if cache_key not in _fx_cache:
        end = pd.Timestamp(datetime.now(TORONTO_TZ).date())
        start = min(
            end - pd.Timedelta(days=FX_WINDOW_DAYS),
            pd.Timestamp(START) - pd.Timedelta(days=14),
        )
        dates = pd.bdate_range(start, end)
        # Generate mock forex dataframe
        fx_df = pd.DataFrame(
            {
                Column.FX.DATE: dates.strftime("%Y-%m-%d"),
                # A deterministic ripple through a plausible USD/CAD band.
                Column.FX.FXUSDCAD: [
                    round(1.35 + 0.005 * (i % 7), 10) for i in range(len(dates))
                ],
            },
        )
        fx_df[Column.FX.FXCADUSD] = (1.0 / fx_df[Column.FX.FXUSDCAD]).round(10)
        _fx_cache[cache_key] = fx_df

    def get_fx_data(start_date: str | None = None) -> pd.DataFrame:
        df = _fx_cache[cache_key].copy()
        if start_date is not None:
            df = df[df[Column.FX.DATE] >= start_date]
        return df

    return get_fx_data


@pytest.fixture(scope="session")
def cached_demo(
    tmp_path_factory: pytest.TempPathFactory,
    cached_fx_data: Callable[[str | None], pd.DataFrame],
    preload_settlement_schedules: None,  # noqa: ARG001 - built over its calendars
) -> Path:
    """Build the real demo folio once per session, for tests to copy.

    Built through the real pipeline, with the synthetic rates standing in for
    the Bank of Canada, so the copied folio carries FX for its whole span.

    Returns:
        The data folder holding the built folio's database and Parquet files.
    """
    root = tmp_path_factory.mktemp("demo_cache")
    (root / "config.yaml").write_text(yaml.safe_dump({"backup": {"enabled": False}}))
    AppContext.reset_singleton()
    AppContext.get_instance().initialize(root)
    try:
        with patch.object(
            ForexService,
            "get_fx_rates_from_boc",
            side_effect=cached_fx_data,
        ):
            DemoFolio().build()
        return get_config().data_path
    finally:
        AppContext.reset_singleton()


@pytest.fixture(autouse=True)
def demo_from_cache(cached_demo: Path) -> Generator[None, Any]:
    """Copy the session's demo folio in, instead of building it again.

    `DemoFolio.ensure` still runs for real, so its guards are what a test sees;
    only the build behind it is swapped.
    """

    def copy_cached(_self: DemoFolio) -> None:
        config = get_config()
        config.data_path.mkdir(parents=True, exist_ok=True)
        for path in (
            config.db_path,
            config.txn_parquet,
            config.tkr_parquet,
            config.fx_parquet,
        ):
            shutil.copy2(cached_demo / path.name, path)

    with patch.object(DemoFolio, "build", copy_cached):
        yield


@pytest.fixture(scope="session", autouse=True)
def preload_settlement_schedules() -> None:
    """Pre-load settlement calculator schedules once for the entire test suite.

    This fixture runs once per test session and caches market calendar schedules
    to minimize pandas_market_calendars API interactions during tests.
    """
    logger = logging.getLogger(__name__)
    today = pd.Timestamp(datetime.now(TORONTO_TZ).date())
    buffer_start = pd.Timestamp(START) - pd.Timedelta(days=10)
    buffer_end = max(pd.Timestamp(END), today) + pd.Timedelta(days=30)

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
