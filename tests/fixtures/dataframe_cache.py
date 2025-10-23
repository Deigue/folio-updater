"""DataFrame caching system for short circuiting file I/O."""

# ruff: noqa: ANN001,ANN202,ANN003

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Self
from unittest.mock import patch

import pandas as pd
import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

logger = logging.getLogger(__name__)


class DataFrameCache:
    """Cache for storing DataFrames by filename key."""

    def __init__(self) -> None:
        """Initialize empty cache."""
        self._cache: dict[str, pd.DataFrame] = {}
        self._sheet_cache: dict[str, dict[str, pd.DataFrame]] = {}

    def register_dataframe(
        self,
        filename: str | Path,
        df: pd.DataFrame,
        sheet_name: str = "Sheet1",
    ) -> None:
        """Register a DataFrame under a filename key.

        Args:
            filename: The filename key that will be used to retrieve this DataFrame
            df: The DataFrame to cache
            sheet_name: Sheet name for Excel files (default: "Sheet1")
        """
        key = str(filename)
        self._cache[key] = df.copy()

        if key not in self._sheet_cache:
            self._sheet_cache[key] = {}
        self._sheet_cache[key][sheet_name] = df.copy()

        # Create an empty file for existence checks
        file_path = Path(filename)
        if not file_path.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.touch()

        logger.debug("Registered DataFrame for key: %s (sheet: %s)", key, sheet_name)

    def get_dataframe(
        self,
        filename: str | Path,
        sheet_name: str | None = None,
    ) -> pd.DataFrame:
        """Retrieve a DataFrame by filename key.

        Args:
            filename: The filename key to look up
            sheet_name: Optional sheet name for Excel files

        Returns:
            DataFrame copy from cache

        Raises:
            KeyError: If filename key not found in cache
        """
        key = str(filename)

        if (
            sheet_name
            and key in self._sheet_cache
            and sheet_name in self._sheet_cache[key]
        ):
            logger.debug("Retrieved DataFrame for key: %s (sheet: %s)", key, sheet_name)
            return self._sheet_cache[key][sheet_name].copy()

        if key in self._cache:
            logger.debug("Retrieved DataFrame for key: %s", key)
            return self._cache[key].copy()

        # Try map against file name instead of full path
        filename_only = Path(key).name
        if filename_only in self._cache:
            logger.debug("Retrieved DataFrame for filename: %s", filename_only)
            return self._cache[filename_only].copy()

        available_keys = list(self._cache.keys())
        error_msg = (
            f"DataFrame not found for key: {key}. Available keys: {available_keys}"
        )
        raise KeyError(error_msg)

    def clear(self) -> None:
        """Clear all cached DataFrames."""
        self._cache.clear()
        self._sheet_cache.clear()
        logger.debug("Cleared DataFrame cache")

    def has_key(self, filename: str | Path) -> bool:
        """Check if a key exists in the cache."""
        key = str(filename)
        return key in self._cache or Path(key).name in self._cache


# Global cache instance
_dataframe_cache = DataFrameCache()


def register_test_dataframe(
    filename: str | Path,
    df: pd.DataFrame,
    sheet_name: str = "Sheet1",
) -> None:
    """Register a DataFrame against a file.

    This is the main function tests should use to cache DataFrames.

    Args:
        filename: The filename key that import_transactions will use
        df: The DataFrame to cache
        sheet_name: Sheet name for Excel files
    """
    _dataframe_cache.register_dataframe(filename, df, sheet_name)


def clear_test_dataframes() -> None:
    """Clear all cached test DataFrames."""
    _dataframe_cache.clear()


@pytest.fixture(autouse=True)
def auto_clear_dataframe_cache() -> Generator[None]:
    """Automatically clear the DataFrame cache before each test."""
    _dataframe_cache.clear()
    yield
    _dataframe_cache.clear()


@pytest.fixture
def dataframe_cache_patching() -> Generator[None]:  # noqa: C901
    """Patch pandas read operations to use the DataFrame cache.

    This fixture patches pandas.read_excel and pandas.read_csv to check
    the DataFrame cache first before attempting actual file I/O.
    """

    def mock_read_excel(io, sheet_name=None, **kwargs):
        """Mock read_excel that checks cache first."""
        try:
            return _dataframe_cache.get_dataframe(io, sheet_name)
        except KeyError:
            # If not in cache, fall back to original function
            # This preserves existing file-based tests like test_generate_command
            if sheet_name is None:
                result = _original_read_excel(io, **kwargs)
            else:
                result = _original_read_excel(io, sheet_name=sheet_name, **kwargs)

            if isinstance(result, dict):
                if sheet_name and sheet_name in result:
                    return result[sheet_name]
                # Fall back to first sheet
                return next(iter(result.values()))
            return result

    def mock_read_csv(filepath_or_buffer, **kwargs):
        """Mock read_csv that checks cache first."""
        try:
            return _dataframe_cache.get_dataframe(filepath_or_buffer)
        except KeyError:
            return _original_read_csv(filepath_or_buffer, **kwargs)

    class MockExcelFile:
        """Mock ExcelFile that returns sheet names for cached files."""

        def __init__(self, io, engine=None, **kwargs) -> None:
            self.io = io
            self.engine = engine
            try:
                _dataframe_cache.get_dataframe(io)
                self.sheet_names = ["Sheet1"]
            except KeyError:
                # If not in cache, use the real ExcelFile
                self._real_file = _original_excel_file(io, engine=engine, **kwargs)
                self.sheet_names = self._real_file.sheet_names

        def __enter__(self) -> Self:
            return self

        def __exit__(self, exc_type, exc_val, exc_tb) -> None:
            # If we loaded a real file, delegate cleanup procedures...
            if hasattr(self, "_real_file"):
                return self._real_file.__exit__(exc_type, exc_val, exc_tb)
            return None

    # Store original functions for fallback
    _original_read_excel = pd.read_excel
    _original_read_csv = pd.read_csv
    _original_excel_file = pd.ExcelFile

    with (
        patch("pandas.read_excel", side_effect=mock_read_excel),
        patch("pandas.read_csv", side_effect=mock_read_csv),
        patch("pandas.ExcelFile", side_effect=MockExcelFile),
        patch("importers.excel_importer.pd.read_excel", side_effect=mock_read_excel),
        patch("importers.excel_importer.pd.read_csv", side_effect=mock_read_csv),
        patch("importers.excel_importer.pd.ExcelFile", side_effect=MockExcelFile),
    ):
        yield
