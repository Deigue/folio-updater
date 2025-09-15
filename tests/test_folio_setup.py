"""Tests for folio setup functionality.

This module contains tests including creation, validation, and error handling of folio
files.

"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable

import pandas as pd
import pandas.testing as pd_testing
import pytest

from mock.folio_setup import ensure_folio_exists
from mock.mock_data import generate_transactions
from utils.constants import DEFAULT_TICKERS, Column

if TYPE_CHECKING:
    from contextlib import _GeneratorContextManager
    from pathlib import Path

    from app.app_context import AppContext

logger: logging.Logger = logging.getLogger(__name__)


def test_folio_creation(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test if default folio can be automatically created.

    Ensures that the folio is created with the expected structure, is not recreated if
    it already exists.
    """
    with temp_config() as ctx:
        config = ctx.config
        folio_path: Path = config.folio_path
        logger.debug("Folio file path: %s", folio_path)
        assert not folio_path.exists()
        ensure_folio_exists()
        assert folio_path.exists()

        # Structure validation
        with pd.ExcelFile(folio_path) as folio:
            assert set(folio.sheet_names) >= {
                config.tickers_sheet(),
                config.transactions_sheet(),
            }
            # Tickers sheet validation
            tickers_df: pd.DataFrame = pd.read_excel(folio, config.tickers_sheet())
            assert tickers_df.shape == (len(DEFAULT_TICKERS), 1)
            assert set(tickers_df.columns) == {Column.Ticker.TICKER}
            assert tickers_df[Column.Ticker.TICKER].tolist() == DEFAULT_TICKERS
            # Transactions sheet validation
            txns_df: pd.DataFrame = pd.read_excel(folio, config.transactions_sheet())
            txns_df = txns_df.where(pd.notna(txns_df), None)
            assert not txns_df.empty
            txn_lists = [generate_transactions(ticker) for ticker in DEFAULT_TICKERS]
            expected_txns_df = pd.concat(txn_lists, ignore_index=True)
            expected_txns_df = expected_txns_df.where(
                pd.notna(expected_txns_df),
                None,
            )
            pd_testing.assert_frame_equal(txns_df, expected_txns_df)

        # Capture last modified time
        mtime_before: float = folio_path.stat().st_mtime
        # Wait a bit to ensure detectable mtime change if rewritten
        time.sleep(0.1)
        # Nothing happens when folio already exists
        ensure_folio_exists()
        # Assert file still exists
        assert folio_path.exists()
        # Assert mtime unchanged
        assert folio_path.stat().st_mtime == mtime_before, (
            "File was unexpectedly modified"
        )


def test_folio_missing(
    tmp_path: Path,
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Raise error when non-standard folio path does not exist."""
    missing_path: Path = tmp_path / "nonexistent_folder" / "folio.xlsx"
    assert not missing_path.exists()
    with temp_config({"folio_path": str(missing_path)}):
        with pytest.raises(FileNotFoundError):
            ensure_folio_exists()
        assert not missing_path.exists()
