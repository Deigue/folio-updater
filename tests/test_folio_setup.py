"""Tests for folio setup functionality.

This module contains tests including creation, validation, and error handling of folio
files.

"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import pytest
import yaml
from src import folio_setup
from src.constants import DEFAULT_TICKERS, TXN_ESSENTIALS

if TYPE_CHECKING:
    from types import ModuleType

logger = logging.getLogger(__name__)


def test_folio_created_and_deleted(config_with_temp: tuple[ModuleType, Path]) -> None:
    """Test folio creation.

    Check that the folio is created with the expected structure, is not recreated if it
    already exists, and can be deleted.

    """
    config, path = config_with_temp
    config.load_config()

    # Folio should not exist before
    folio_file: Path = path.parent / "data" / "folio.xlsx"
    if folio_file.exists():
        folio_file.unlink()  # pragma: no cover

    folio_setup.ensure_folio_exists()
    logger.info("Folio file path: %s", folio_file)
    assert folio_file.exists()

    # Structure validation
    with pd.ExcelFile(folio_file) as folio:
        assert set(folio.sheet_names) >= {
            config.tickers_sheet(),
            config.transactions_sheet(),
        }
        tickers_df: pd.DataFrame = pd.read_excel(folio, config.tickers_sheet())
        assert "Ticker" in tickers_df.columns
        for ticker in DEFAULT_TICKERS:
            assert ticker in tickers_df["Ticker"].to_numpy()
        txns_df: pd.DataFrame = pd.read_excel(folio, config.transactions_sheet())
        for col in TXN_ESSENTIALS:
            assert col in txns_df.columns

    # Capture last modified time
    mtime_before: float = folio_file.stat().st_mtime
    # Wait a bit to ensure detectable mtime change if rewritten
    time.sleep(0.1)
    # Nothing happens when folio already exists
    folio_setup.ensure_folio_exists()
    # Assert file still exists
    assert folio_file.exists()
    # Assert mtime unchanged
    assert folio_file.stat().st_mtime == mtime_before, "File was unexpectedly modified"

    # Cleanup
    folio_file.unlink()
    assert not folio_file.exists()


def test_folio_not_exists(config_with_temp: tuple[ModuleType, Path]) -> None:
    """Raise error when folio path does not exist."""
    config, path = config_with_temp
    missing_path = Path(config.PROJECT_ROOT) / "nonexistent_folder" / "folio.xlsx"
    assert not missing_path.exists()

    with Path.open(path, "w") as config_yaml:
        yaml.safe_dump({"folio_path": str(missing_path)}, config_yaml)

    config.load_config()
    with pytest.raises(FileNotFoundError):
        folio_setup.ensure_folio_exists()

    assert not missing_path.exists()
