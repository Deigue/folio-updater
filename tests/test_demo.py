"""Tests for the demo folio: when it is built, and what building it leaves."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd
import pandas.testing as pd_testing
import pytest

from app import get_config
from cli.main import app as cli_app
from datagen import ensure_data_exists
from db import get_alias_edges, get_connection, get_distinct_set, get_rows
from domain import CheckStatus, Column, Table
from engine import cache
from engine.checks import run_checks

from .fixtures.test_data_factory import create_transaction_data
from .helpers.cli import (
    assert_cli_success,
    assert_in_output,
    run_cli_with_config,
)
from .helpers.folio import txn_total
from .helpers.seed import seed_transaction

if TYPE_CHECKING:
    from pathlib import Path

    from .test_types import TempContext

logger: logging.Logger = logging.getLogger(__name__)


def test_data_creation(temp_ctx: TempContext) -> None:
    """The demo leaves its exported files mirroring the database it built."""
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


def test_the_demo_replays_clean(temp_ctx: TempContext) -> None:
    """The demo is a folio with nothing wrong in it: every check passes.

    The generator tracks what the replay checks, so this fails the moment a
    change to the scenario, or to the engine, lets one slip.
    """
    with temp_ctx():
        ensure_data_exists()
        result = cache.build().result
        assert result is not None
        checks = run_checks(result, get_config())

    assert [str(warning) for warning in result.warnings] == []
    assert [
        f"{check.slug}: {check.summary}"
        for check in checks
        if check.status is not CheckStatus.OK
    ] == []


def test_data_creation_skipped_when_db_already_populated(
    temp_ctx: TempContext,
) -> None:
    """Refuse to build the demo when the db has rows but the parquet is missing.

    This can happen if a real transactions.parquet is deleted or moved while
    folio.db still holds real transactions: the demo must not be appended on
    top of it.
    """
    with temp_ctx() as ctx:
        config = ctx.config
        seed_transaction()

        assert not config.txn_parquet.exists()

        created = ensure_data_exists()

        assert created is False
        assert not config.txn_parquet.exists()


def test_a_missing_folio_folder_is_refused(
    tmp_path: Path,
    temp_ctx: TempContext,
) -> None:
    """Only the default data folder is created; any other must already exist."""
    missing_path: Path = tmp_path / "nonexistent_folder" / "folio.xlsx"
    with temp_ctx({"folio_path": str(missing_path)}), pytest.raises(FileNotFoundError):
        ensure_data_exists()
    assert not missing_path.parent.exists()


# -- REPLACING THE DEMO -------------------------------------------------------

_PROMPT = "Replace the demo with your own transactions?"


def _stored_accounts() -> set[str]:
    with get_connection() as conn:
        return get_distinct_set(conn, Table.TXNS, Column.Txn.ACCOUNT)


def test_importing_your_own_transactions_can_replace_the_demo(
    temp_ctx: TempContext,
) -> None:
    """Yes backs the demo up, then your rows start an empty folio from TxnId 1."""
    with temp_ctx(backup={"enabled": True}) as ctx:
        ensure_data_exists()
        source = ctx.config.project_root / "mine.xlsx"
        create_transaction_data(source)

        result = run_cli_with_config(
            ctx.config,
            cli_app,
            ["import", "-f", str(source)],
            user_input="y\n",
        )

        with get_connection() as conn:
            txn_ids = sorted(get_rows(conn, Table.TXNS)[Column.Txn.TXN_ID])
            renames = get_alias_edges(conn)
        backed_up = any(ctx.config.backup_path.rglob("folio_*.db"))
        accounts = _stored_accounts()

    assert_cli_success(result)
    assert _PROMPT in result.stdout
    assert accounts == {"TEST-ACCOUNT"}
    assert txn_ids == [1, 2]
    assert renames == []
    assert backed_up


@pytest.mark.parametrize(
    "answer",
    [pytest.param("n\n", id="no"), pytest.param(None, id="nobody-to-answer")],
)
def test_declining_keeps_the_demo_and_imports_nothing(
    temp_ctx: TempContext,
    answer: str | None,
) -> None:
    with temp_ctx() as ctx:
        ensure_data_exists()
        before = txn_total()
        source = ctx.config.project_root / "mine.xlsx"
        create_transaction_data(source)

        result = run_cli_with_config(
            ctx.config,
            cli_app,
            ["import", "-f", str(source)],
            user_input=answer,
        )

        after = txn_total()
        accounts = _stored_accounts()
        left_in_place = source.exists()

    assert result.exit_code == 1
    assert_in_output("the demo portfolio is unchanged", result)
    assert after == before
    assert "TEST-ACCOUNT" not in accounts
    assert left_in_place


def test_a_folio_with_an_account_of_your_own_is_not_the_demo(
    temp_ctx: TempContext,
) -> None:
    """Once you have added your own account, imports join the folio as usual."""
    with temp_ctx() as ctx:
        ensure_data_exists()
        seed_transaction()
        before = txn_total()
        source = ctx.config.project_root / "mine.xlsx"
        create_transaction_data(source)

        result = run_cli_with_config(ctx.config, cli_app, ["import", "-f", str(source)])

        after = txn_total()

    assert_cli_success(result)
    assert _PROMPT not in result.stdout
    assert after == before + 2


def test_a_directory_import_asks_before_touching_the_demo(
    temp_ctx: TempContext,
) -> None:
    """`folio import` with no file reads the imports folder, and asks the same."""
    with temp_ctx() as ctx:
        ensure_data_exists()
        before = txn_total()
        create_transaction_data(ctx.config.imports_path / "mine.xlsx")

        result = run_cli_with_config(ctx.config, cli_app, ["import"], user_input="n\n")

        after = txn_total()

    assert result.exit_code == 1
    assert _PROMPT in result.stdout
    assert after == before
