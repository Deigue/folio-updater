"""Tests for the demo folio: when it is built, and what building it leaves."""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd
import pandas.testing as pd_testing
import pytest

from app import get_config
from cli.main import app as cli_app
from datagen import DemoFolio, DemoScenarioError, ensure_data_exists
from datagen.demo import PERSONAL, START, USDCAD, _Simulation, price_on
from datagen.history import _window_events
from db import (
    get_alias_edges,
    get_connection,
    get_distinct_set,
    get_rows,
)
from domain import AccountType, Action, CheckStatus, Column, Table
from engine.checks import run_checks
from engine.ledger import open_ledger
from exporters import ParquetExporter
from services import ForexService

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


def _showcase(
    txns: pd.DataFrame,
    frame: pd.DataFrame,
    renames: list[tuple[str, str, str]],
) -> dict[str, bool]:
    """List whether each feature the demo exists to show actually exists.

    Args:
        txns: The stored transactions.
        frame: The replay's ledger, one row per transaction.
        renames: The stored ticker renames, as (old, new, effective date).

    Returns:
        Each feature, named, and whether the demo shows it.
    """
    action, account = txns[Column.Txn.ACTION], txns[Column.Txn.ACCOUNT]
    ticker, day = txns[Column.Txn.TICKER], txns[Column.Txn.TXN_DATE]
    trades = txns[action.isin([Action.BUY, Action.SELL])]
    out, into = txns[action == Action.TFR_OUT], txns[action == Action.TFR_IN]
    paid = set(zip(day[action == Action.DIVIDEND], account, ticker, strict=False))
    # Cash legs pair by size, as the replay pairs them: they land days apart.
    cash_out, cash_in = (
        legs[legs[Column.Txn.TICKER].isna()].assign(
            Size=legs[Column.Txn.AMOUNT].abs(),
        )
        for legs in (out, into)
    )
    sells = frame[frame[Column.Txn.ACTION] == Action.SELL]
    last = frame.groupby([Column.Txn.ACCOUNT, "Symbol"]).tail(1)
    taxable_gains = last.loc[last["AcctType"] == AccountType.NON_REGISTERED, "AcctGain"]
    rrsp_deposits = txns[
        (action == Action.CONTRIBUTION) & account.str.endswith("RRSP")
    ][Column.Txn.TXN_DATE]
    return {
        "every kind of transaction": set(action) == {str(a) for a in Action},
        "one account type held at two brokers": bool(
            account.drop_duplicates().str.split("-").str[-1].duplicated().any(),
        ),
        "a split recorded in each account holding it": bool(
            (txns[action == Action.SPLIT].groupby([day, ticker]).size() > 1).any(),
        ),
        "a ticker traded under its old and its new name": any(
            {old, new} <= set(ticker) for old, new, _ in renames
        ),
        "settlement dates both calculated and from the broker": (
            set(trades[Column.Txn.SETTLE_CALCULATED].astype(int)) == {0, 1}
        ),
        "dividends reinvested on the day they are paid": any(
            (row.TxnDate, row.Account, row.Ticker) in paid
            for row in trades[trades[Column.Txn.FEE].isna()].itertuples()
        ),
        "a currency journal (Norbert's gambit)": not out.merge(
            into,
            on=[Column.Txn.TXN_DATE, Column.Txn.ACCOUNT],
        )
        .query("Ticker_x != Ticker_y")
        .empty,
        "units moved in kind between accounts": not out.merge(
            into,
            on=Column.Txn.TICKER,
        )
        .query("Account_x != Account_y")
        .empty,
        "cash moved between accounts": not cash_out.merge(cash_in, on="Size")
        .query("Account_x != Account_y")
        .empty,
        "a taxable gain and a taxable loss": (
            bool((taxable_gains > 0).any()) and bool((taxable_gains < 0).any())
        ),
        "a holding sold out, and one only partly sold": (
            bool((sells["AcctUnits"] == 0).any())
            and bool((sells["AcctUnits"] > 0).any())
        ),
        "an RRSP deposit counted toward the previous year": bool(
            rrsp_deposits.str[5:7].isin(["01", "02"]).any(),
        ),
    }


def test_the_demo_shows_every_feature_it_exists_to_show(
    temp_ctx: TempContext,
) -> None:
    """A price history refresh or a script edit must not quietly lose a showcase.

    The clean replay cannot see this: a demo with no splits still replays clean.
    """
    with temp_ctx():
        ensure_data_exists()
        with get_connection() as conn:
            txns = get_rows(conn, Table.TXNS)
            renames = get_alias_edges(conn)
        features = _showcase(txns, open_ledger().frame, renames)

    assert [name for name, shown in features.items() if not shown] == []


def test_the_demo_replays_clean(temp_ctx: TempContext) -> None:
    """The demo is a folio with nothing wrong in it: every check passes.

    The generator tracks what the replay checks, so this fails the moment a
    change to the scenario, or to the engine, lets one slip.
    """
    with temp_ctx():
        ensure_data_exists()
        ledger = open_ledger()
        checks = run_checks(ledger, get_config())

    assert [str(diagnostic) for diagnostic in ledger.diagnostics] == []
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
    assert_in_output("the demo folio is unchanged", result)
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


# -- BUILDING THE DEMO --------------------------------------------------------

# The autouse cache fixture swaps `DemoFolio.build` out for every test.
_REAL_BUILD = DemoFolio.build


def test_a_build_that_fails_part_way_leaves_the_folio_empty(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The demo rows are removed again, so the next `folio demo` is not refused."""

    def disk_full(_self: ParquetExporter) -> None:
        msg = "disk full"
        raise OSError(msg)

    with temp_ctx():
        monkeypatch.setattr(ForexService, "ensure_coverage", lambda *_a, **_k: 0)
        monkeypatch.setattr(ParquetExporter, "export_all", disk_full)

        with pytest.raises(OSError, match="disk full"):
            _REAL_BUILD(DemoFolio())

        assert ensure_data_exists() is True


# -- THE SIMULATION -----------------------------------------------------------


def test_a_sale_scheduled_by_keyword_still_blacks_out_nearby_purchases() -> None:
    """The superficial-loss guard reads the sale's arguments, not how it was spelled."""
    sim = _Simulation()
    sim.at(START, sim.contribute, PERSONAL, 10_000)
    sim.at(date(2025, 1, 6), sim.buy, PERSONAL, "AAPL", units=10)
    sim.at(date(2025, 2, 3), sim.sell, account=PERSONAL, symbol="AAPL")
    sim.at(date(2025, 2, 10), sim.buy, PERSONAL, "AAPL", units=1)

    with pytest.raises(DemoScenarioError, match="superficial loss"):
        sim.run()


def _cost(sim: _Simulation, symbol: str) -> Decimal:
    """Read the cost base the simulation holds for the personal account."""
    return sim._cost[(PERSONAL.name, symbol)]  # noqa: SLF001


def test_a_partial_journal_moves_only_its_share_of_the_cost() -> None:
    """The units left behind keep their cost, and a second journal adds to the first."""
    sim = _Simulation()
    day = date(2025, 3, 3)
    sim.contribute(START, PERSONAL, 10_000)
    sim.buy(day, PERSONAL, "DLR.TO", units=100)
    cost = _cost(sim, "DLR.TO")
    rate = price_on(USDCAD, day)

    sim.journal(day, PERSONAL, 40)
    assert _cost(sim, "DLR.TO") == cost * 60 / 100
    assert _cost(sim, "DLR.U.TO") == cost * 40 / 100 / rate

    sim.journal(day, PERSONAL, 60)
    assert _cost(sim, "DLR.TO") == Decimal(0)
    assert _cost(sim, "DLR.U.TO") == cost * 40 / 100 / rate + cost * 60 / 100 / rate


# -- THE PRICE HISTORY TOOL ---------------------------------------------------


def test_a_split_after_the_window_is_kept_for_the_price_history() -> None:
    """Stored closes are adjusted for it, so reading them back has to know of it."""
    series = pd.Series(
        {
            pd.Timestamp("2024-09-12", tz="America/New_York"): 4.0,
            pd.Timestamp("2025-01-10", tz="America/New_York"): 3.0,
            pd.Timestamp("2026-11-03", tz="America/New_York"): 2.0,
        },
    )

    assert _window_events(series, 4) == ["2025-01-10:3"]
    assert _window_events(series, 4, until=date.max) == [
        "2025-01-10:3",
        "2026-11-03:2",
    ]
