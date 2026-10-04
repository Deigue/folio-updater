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

from datagen import ensure_data_exists
from db import get_connection, get_distinct_set
from domain import Column, Table

from .conftest import _original_ensure_data_exists
from .helpers.folio import txn_total
from .helpers.seed import seed_transaction

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

        # The exported files mirror the database, whatever the demo holds.
        with get_connection() as conn:
            stored_tickers = get_distinct_set(conn, Table.TXNS, Column.Txn.TICKER)
        tickers_df = pd.read_parquet(config.tkr_parquet, engine="fastparquet")
        assert set(tickers_df.columns) == {Column.Ticker.TICKER}
        assert set(tickers_df[Column.Ticker.TICKER]) == stored_tickers

        txns_df = pd.read_parquet(config.txn_parquet, engine="fastparquet")
        assert not txns_df.empty
        assert len(txns_df) == txn_total()

        # Repeat call data remains same.
        ensure_data_exists()
        txns_df_2 = pd.read_parquet(config.txn_parquet, engine="fastparquet")
        pd_testing.assert_frame_equal(txns_df, txns_df_2)


def test_data_creation_skipped_when_db_already_populated(
    temp_ctx: TempContext,
) -> None:
    """Refuse to generate mock data when the db has rows but the parquet is missing.

    This can happen if a real transactions.parquet is deleted or moved while
    folio.db still holds real transactions: mock data must not be appended on
    top of it.
    """
    with temp_ctx() as ctx:
        config = ctx.config
        seed_transaction()

        assert not config.txn_parquet.exists()

        created = _original_ensure_data_exists(mock=True)

        assert created is False
        assert not config.txn_parquet.exists()


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
        foliosetup_level = logging.getLogger("datagen.folio_setup").getEffectiveLevel()
        logging.getLogger("tests.conftest").setLevel(logging.CRITICAL)
        logging.getLogger("datagen.folio_setup").setLevel(logging.CRITICAL)
        with pytest.raises(FileNotFoundError):
            ensure_data_exists(mock=mock)
        assert not missing_path.exists()
        logging.getLogger("tests.conftest").setLevel(conftest_level)
        logging.getLogger("datagen.folio_setup").setLevel(foliosetup_level)
