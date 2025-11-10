"""Tests for folio setup functionality.

This module contains tests including creation, validation, and error handling of folio
files.

"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd
import pandas.testing as pd_testing
import pytest

from mock.folio_setup import ensure_data_exists
from mock.mock_data import generate_transactions
from utils.constants import DEFAULT_TICKERS, Column

if TYPE_CHECKING:
    from pathlib import Path

    from .test_types import TempContext

logger: logging.Logger = logging.getLogger(__name__)


def test_data_creation(temp_ctx: TempContext) -> None:
    """Test default mock data creation."""
    with temp_ctx() as ctx:
        config = ctx.config
        txn_parquet: Path = config.txn_parquet
        logger.debug("Transaction data file: %s", txn_parquet)
        assert not txn_parquet.exists()

        ensure_data_exists()
        assert config.txn_parquet.exists()
        assert config.tkr_parquet.exists()
        # * forex is tested separately

        tickers_df = pd.read_parquet(config.tkr_parquet, engine="fastparquet")
        assert tickers_df.shape == (len(DEFAULT_TICKERS), 1)
        assert set(tickers_df.columns) == {Column.Ticker.TICKER}
        assert sorted(tickers_df[Column.Ticker.TICKER].tolist()) == sorted(
            DEFAULT_TICKERS,
        )

        txns_df = pd.read_parquet(config.txn_parquet, engine="fastparquet")
        txns_df = txns_df.where(pd.notna(txns_df), None)
        assert not txns_df.empty
        txn_lists = [generate_transactions(ticker) for ticker in DEFAULT_TICKERS]
        expected_txns_df = pd.concat(txn_lists, ignore_index=True)
        expected_txns_df = expected_txns_df.where(
            pd.notna(expected_txns_df),
            None,
        )
        # Don't compare auto-calculated fields
        txns_df_clean = txns_df.drop(
            columns=[Column.Txn.SETTLE_DATE, Column.Txn.SETTLE_CALCULATED],
            errors="ignore",
        )
        pd_testing.assert_frame_equal(txns_df_clean, expected_txns_df)

        # Repeat call data remains same.
        ensure_data_exists()
        txns_df_2 = pd.read_parquet(config.txn_parquet, engine="fastparquet")
        pd_testing.assert_frame_equal(txns_df, txns_df_2)


@pytest.mark.parametrize(
    ("path_suffix", "mock"),
    [
        ("nonexistent_folder/folio.xlsx", True),
        ("nonexistent_file.xlsx", False),
    ],
)
def test_error_scenarios(
    tmp_path: Path,
    temp_ctx: TempContext,
    path_suffix: str,
    *,
    mock: bool,
) -> None:
    """Raise FileNotFoundError for various error scenarios."""
    missing_path: Path = tmp_path / path_suffix
    assert not missing_path.exists()
    with temp_ctx({"folio_path": str(missing_path)}):
        conftest_level = logging.getLogger("tests.conftest").getEffectiveLevel()
        foliosetup_level = logging.getLogger("mock.folio_setup").getEffectiveLevel()
        logging.getLogger("tests.conftest").setLevel(logging.CRITICAL)
        logging.getLogger("mock.folio_setup").setLevel(logging.CRITICAL)
        with pytest.raises(FileNotFoundError):
            ensure_data_exists(mock=mock)
        assert not missing_path.exists()
        logging.getLogger("tests.conftest").setLevel(conftest_level)
        logging.getLogger("mock.folio_setup").setLevel(foliosetup_level)
