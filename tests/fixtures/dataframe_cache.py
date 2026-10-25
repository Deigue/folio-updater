"""DataFrame caching system for short circuiting file I/O."""

# ruff: noqa: ANN001,ANN202,ANN003

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING
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


# One cache per worker process. Each test registers under its own tmp_path, so
# no frame is shared between tests; clearing on teardown only frees them.
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


@pytest.fixture(autouse=True)
def dataframe_cache_patching() -> Generator[None]:
    """Patch pandas read operations to use the DataFrame cache.

    Patches pandas.read_excel and pandas.read_csv to check the DataFrame cache
    first, falling back to real file I/O for anything no test registered (a
    workbook `generate` really wrote, a Bank of Canada response in a `StringIO`).
    """

    def mock_read_excel(io, sheet_name=None, **kwargs):
        """Mock read_excel that checks cache first."""
        try:
            return _dataframe_cache.get_dataframe(io, sheet_name)
        except KeyError:
            if sheet_name is None:
                return _original_read_excel(io, **kwargs)
            return _original_read_excel(io, sheet_name=sheet_name, **kwargs)

    def mock_read_csv(filepath_or_buffer, **kwargs):
        """Mock read_csv that checks cache first."""
        try:
            return _dataframe_cache.get_dataframe(filepath_or_buffer)
        except KeyError:
            return _original_read_csv(filepath_or_buffer, **kwargs)

    # Store original functions for fallback
    _original_read_excel = pd.read_excel
    _original_read_csv = pd.read_csv

    # `importers.excel_importer` does `import pandas as pd`, so patching the
    # `pandas` attributes reaches it too.
    with (
        patch("pandas.read_excel", side_effect=mock_read_excel),
        patch("pandas.read_csv", side_effect=mock_read_csv),
    ):
        yield
    _dataframe_cache.clear()
