"""Tests for `folio acb` and `folio symbol`."""

from __future__ import annotations

import csv
import json
import shutil
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import patch

import pandas as pd
import pytest
import yaml

from app import get_config, initialize_app
from cli.main import app
from db import (
    aliases,
    create_ticker_aliases_table,
    delete_rows,
    drop_table,
    get_connection,
    get_row_count,
    get_rows,
    get_triggers,
    quotes,
    update_rows,
)
from domain import Column, QuoteStatus, Table
from engine.ledger import open_ledger
from models import Quote, TickerAlias

from .helpers.cli import (
    assert_cli_success,
    assert_in_output,
    assert_not_in_output,
    run_cli_with_config,
)
from .helpers.seed import TICKER, seed_cash, seed_fx, seed_transaction

if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path

    from app import AppContext

    from .fixtures.fake_quote_provider import FakeQuoteProvider
    from .helpers.context import TempContext

FX = {
    "2025-08-14": "1.3500",
    "2025-08-15": "1.3600",
    "2025-08-18": "1.3700",
}


def seed_cad_holding(account: str = "IBKR-PERSONAL", ticker: str = "RY.TO") -> None:
    """Seed a plain CAD buy-then-sell in one account."""
    seed_fx(FX)
    seed_transaction(
        action="BUY",
        date="2025-08-14",
        settle_date="2025-08-14",
        account=account,
        currency="CAD",
        ticker=ticker,
        amount="-1000",
        price="10",
        units="100",
    )
    seed_transaction(
        action="SELL",
        date="2025-08-15",
        settle_date="2025-08-15",
        account=account,
        currency="CAD",
        ticker=ticker,
        amount="600",
        price="12",
        units="-50",
    )


def seed_usd_holding(account: str = "IBKR-PERSONAL", ticker: str = "MSFT") -> None:
    """Seed a USD buy, so the `_USD` column family has something in it."""
    seed_fx(FX)
    seed_transaction(
        action="BUY",
        date="2025-08-14",
        settle_date="2025-08-14",
        account=account,
        currency="USD",
        ticker=ticker,
        amount="-1000",
        price="100",
        units="10",
    )


def seed_superficial_loss(
    account: str = "IBKR-PERSONAL",
    ticker: str = "RY.TO",
) -> None:
    """Seed a loss sale followed by a repurchase inside the CRA window."""
    seed_fx(FX)
    seed_transaction(
        action="BUY",
        date="2025-08-14",
        settle_date="2025-08-14",
        account=account,
        currency="CAD",
        ticker=ticker,
        amount="-1000",
        price="10",
        units="100",
    )
    seed_transaction(
        action="SELL",
        date="2025-08-15",
        settle_date="2025-08-15",
        account=account,
        currency="CAD",
        ticker=ticker,
        amount="400",
        price="8",
        units="-50",
    )
    seed_transaction(
        action="BUY",
        date="2025-08-18",
        settle_date="2025-08-18",
        account=account,
        currency="CAD",
        ticker=ticker,
        amount="-400",
        price="8",
        units="50",
    )


@pytest.mark.parametrize(
    "request_args",
    [["RY.TO"], ["--export", "{target}"]],
    ids=["on-screen", "export"],
)
def test_acb_currency_usd_against_a_cad_holding_is_an_error(
    temp_ctx: TempContext,
    tmp_path: Path,
    request_args: list[str],
) -> None:
    """An export would be every figure blank, so it is refused and nothing written."""
    target = tmp_path / "acb.csv"
    args = [arg.format(target=target) for arg in request_args]
    with temp_ctx() as ctx:
        seed_cad_holding()
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", *args, "--currency", "USD"],
        )
        assert not target.exists()
    assert result.exit_code == 1
    assert_in_output("Nothing here is in USD", result)


def test_acb_reports_the_conversion_basis(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_usd_holding()
        result = run_cli_with_config(ctx.config, app, ["acb", "MSFT"])
    assert_in_output("settlement date", result)


def test_acb_account_grain(temp_ctx: TempContext) -> None:
    """One account holds its own 50 units; the TFSA type pools both, 550."""
    with temp_ctx() as ctx:
        seed_cad_holding(account="IBKR-TFSA")
        seed_transaction(
            action="BUY",
            date="2025-08-14",
            settle_date="2025-08-14",
            account="WS-TFSA",
            currency="CAD",
            ticker="RY.TO",
            amount="-5000",
            price="10",
            units="500",
        )
        single = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "RY.TO", "--account", "IBKR-TFSA"],
        )
        pooled = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "RY.TO", "--type", "tfsa"],
        )
    assert_cli_success(single)
    assert_cli_success(pooled)
    assert_not_in_output("550", single)
    assert_in_output("550", pooled)


def test_acb_folio_grain(temp_ctx: TempContext) -> None:
    """The folio pools a TFSA's 50 units with a taxable account's 50."""
    with temp_ctx() as ctx:
        seed_cad_holding(account="IBKR-TFSA")
        seed_cad_holding(account="IBKR-PERSONAL")
        portfolio = run_cli_with_config(ctx.config, app, ["acb", "RY.TO", "-t", "all"])
        taxable = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
    # Only the pooled view ever holds both buys at once.
    assert_cli_success(portfolio)
    assert_in_output("Portfolio", portfolio)
    assert_in_output("200", portfolio)
    assert_cli_success(taxable)
    assert_not_in_output("200", taxable)


def test_acb_unknown_type_errors(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding()
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "RY.TO", "--type", "banana"],
        )
    assert result.exit_code == 1
    assert_in_output("Unknown account type", result)


def test_acb_hides_income_rows_unless_all(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding()
        seed_transaction(
            action="DIVIDEND",
            date="2025-08-18",
            settle_date="2025-08-18",
            account="IBKR-PERSONAL",
            currency="CAD",
            ticker="RY.TO",
            amount="25",
            price=None,
            units=None,
        )
        without = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
        with_all = run_cli_with_config(ctx.config, app, ["acb", "RY.TO", "--all"])
    assert_not_in_output("DIVIDEND", without)
    assert_in_output("DIVIDEND", with_all)


def test_acb_year_filter(temp_ctx: TempContext) -> None:
    """A year keeps its own rows and drops the rest; an empty year says so."""
    with temp_ctx() as ctx:
        seed_cad_holding()
        seed_transaction(
            action="BUY",
            date="2024-08-14",
            settle_date="2024-08-14",
            account="IBKR-PERSONAL",
            currency="CAD",
            ticker="RY.TO",
            amount="-900",
            price="9",
            units="100",
        )
        this_year = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "RY.TO", "--year", "2025"],
        )
        empty = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "RY.TO", "--year", "2020"],
        )
    assert_cli_success(this_year)
    assert_in_output("2025-08-14", this_year)
    assert_not_in_output("2024-08-14", this_year)
    assert_cli_success(empty)
    assert_in_output("No RY.TO transactions", empty)


def test_acb_summary(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding()
        seed_usd_holding()
        result = run_cli_with_config(ctx.config, app, ["acb", "--summary"])
    assert_cli_success(result)
    assert_in_output("Cost base summary", result)
    assert_in_output("RY.TO", result)
    assert_in_output("MSFT", result)


def test_acb_requires_a_symbol_without_summary_or_export(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding()
        result = run_cli_with_config(ctx.config, app, ["acb", "-t", "nreg"])
    assert result.exit_code == 1
    assert_in_output("SYMBOL is required", result)


def _exported(target: Path) -> list[list[str]]:
    """Read an exported CSV back as rows, stopping at the spacer.

    The notes below the table are disclosure rather than data, and a reader of
    the grid wants the grid.
    """
    lines = target.read_text(encoding="utf-8").splitlines()
    rows: list[list[str]] = []
    for line in lines:
        cells = next(csv.reader([line]))
        if not any(cells):
            break
        rows.append(cells)
    return rows


def test_acb_export_writes_the_buildup_it_reports(
    temp_ctx: TempContext,
    tmp_path: Path,
) -> None:
    target = tmp_path / "acb.csv"
    with temp_ctx() as ctx:
        seed_cad_holding()
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "RY.TO", "--export", str(target)],
        )
        assert_cli_success(result)
        # The file is the report: nothing is printed beside it.
        assert_not_in_output("ACB - RY.TO", result)
        # Asserted inside the context: `temp_ctx` sweeps *.csv on the way out.
        assert target.exists()
        header, *rows = _exported(target)
        # A buildup is per transaction, so it carries the TxnId of each.
        assert "TxnId" in header
        assert len(rows) == 2


def test_acb_export_writes_txn_ids_as_plain_digits(
    temp_ctx: TempContext,
    tmp_path: Path,
) -> None:
    """A TxnId is read back by `folio query` and `folio edit`, which take no comma."""
    target = tmp_path / "acb.csv"
    with temp_ctx() as ctx:
        seed_fx(FX)
        seed_transaction(
            action="BUY",
            date="2025-03-11",
            settle_date="2025-03-11",
            account="IBKR-PERSONAL",
            currency="CAD",
            ticker="RY.TO",
            amount="-730",
            price="14.6",
            units="50",
        )
        # The only row, renumbered past the point a thousands separator appears.
        with get_connection() as conn:
            update_rows(
                conn,
                Table.TXNS,
                [{Column.Txn.TICKER: "RY.TO", Column.Txn.TXN_ID: 4321}],
                [Column.Txn.TICKER],
                [Column.Txn.TXN_ID],
            )
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "RY.TO", "--export", str(target)],
        )
        assert_cli_success(result)
        header, *rows = _exported(target)
    assert [row[header.index("TxnId")] for row in rows] == ["4321"]


def test_acb_export_writes_the_summary_when_asked_for_one(
    temp_ctx: TempContext,
    tmp_path: Path,
) -> None:
    """`--summary --export` used to write the buildup regardless."""
    target = tmp_path / "acb.csv"
    with temp_ctx() as ctx:
        seed_cad_holding()
        seed_cad_holding(ticker="TSTKR.TO")
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "--summary", "--export", str(target)],
        )
        assert_cli_success(result)
        header, *rows = _exported(target)
        # One row per symbol, and no per-transaction column in sight.
        assert "TxnId" not in header
        assert [row[0] for row in rows] == ["RY.TO", "TSTKR.TO"]


def test_acb_export_without_a_symbol_writes_the_whole_ledger(
    temp_ctx: TempContext,
    tmp_path: Path,
) -> None:
    target = tmp_path / "acb.csv"
    with temp_ctx() as ctx:
        seed_cad_holding()
        seed_cad_holding(ticker="TSTKR.TO")
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "--export", str(target)],
        )
        assert_cli_success(result)
        header, *rows = _exported(target)
        # Every transaction, with all three pool grains beside it.
        assert len(rows) == 4
        for prefix in ("Acct", "Type", "Folio"):
            assert f"{prefix} ACB" in header


def test_acb_flags_a_seeded_oversell(temp_ctx: TempContext) -> None:
    """A diagnostic reaches the table and the footer, and still exits zero.

    Reporting a problem is not the same as failing: `folio acb` is a reporting
    command, and gating a build on the folio's health belongs to `folio check`.
    """
    with temp_ctx() as ctx:
        seed_fx(FX)
        seed_transaction(
            action="SELL",
            date="2025-08-15",
            settle_date="2025-08-15",
            account="IBKR-PERSONAL",
            currency="CAD",
            ticker="RY.TO",
            amount="600",
            price="12",
            units="-50",
        )
        result = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
    assert_cli_success(result)
    assert_in_output("OVERSELL", result)


def test_acb_flags_a_superficial_loss_suspect(temp_ctx: TempContext) -> None:
    """A loss sale followed by a repurchase in the CRA window is highlighted."""
    with temp_ctx() as ctx:
        seed_superficial_loss()
        result = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
    assert_cli_success(result)
    assert_in_output("SUPERFICIAL_LOSS_SUSPECT", result)


def test_acb_reports_freshness(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_cad_holding()
        first = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
        second = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
    assert_in_output("computed just now", first)
    assert_in_output("cached", second)


# --- saved ledger --------------------------------------------------------------


# The saved ledger's one USD trade settles on 2025-08-18, so that is the latest
# date anything converts at: the FX cutoff.
CUTOFF = "2025-08-18"

type Change = Callable[[int], object]


def _replays() -> bool:
    """Open the ledger, saying whether it was replayed rather than reused.

    Offline, so only the change under test moves the stored FX rates.
    """
    return open_ledger(offline=True).computed_at is None


@pytest.fixture
def saved_txn_id(temp_ctx: TempContext) -> Generator[int]:
    """Build and save the ledger of a one-trade USD folio.

    Yields:
        The TxnId of the trade.
    """
    with temp_ctx():
        seed_fx({"2025-08-14": "1.3500"})
        create_ticker_aliases_table()
        txn_id = seed_transaction(
            date="2025-08-15",
            settle_date=CUTOFF,
            account="IBKR-PERSONAL",
        )
        assert _replays()
        yield txn_id


def _set(column: str, value: str) -> Change:
    """Edit one column of the trade, the way `folio edit --set` does."""

    def change(txn_id: int) -> None:
        with get_connection() as conn:
            update_rows(
                conn,
                Table.TXNS,
                [{Column.Txn.TXN_ID: txn_id, column: value}],
                [Column.Txn.TXN_ID],
                [column],
            )

    return change


def _configure(section: str, value: dict[str, object]) -> Change:
    """Replace one config section on disk and reload it."""

    def change(_: int) -> None:
        config = get_config()
        path = config.config_path
        settings = yaml.safe_load(path.read_text(encoding="utf-8"))
        settings[section] = value
        path.write_text(yaml.safe_dump(settings), encoding="utf-8")
        initialize_app(config.project_root)

    return change


def _write_quote(*_: object) -> None:
    quotes.write(
        [
            Quote(
                symbol=TICKER,
                provider_symbol=TICKER,
                price=Decimal("151.5"),
                status=QuoteStatus.OK,
            ),
        ],
    )


def _add_alias(*_: object) -> None:
    aliases.write(
        [
            TickerAlias(
                old_ticker=TICKER,
                new_ticker="MSFT",
                effective_date="2025-09-01",
            ),
        ],
    )


def _add_transaction(*_: object) -> None:
    seed_transaction(date="2025-08-15", settle_date=CUTOFF, account="IBKR-PERSONAL")


def _delete_transaction(txn_id: int) -> None:
    with get_connection() as conn:
        delete_rows(conn, Table.TXNS, f'"{Column.Txn.TXN_ID}" = ?', (txn_id,))


def _restore_backup_over_an_edit(txn_id: int) -> None:
    db_path = get_config().db_path
    backup = db_path.with_name("backup.db")
    shutil.copy2(db_path, backup)
    _set(Column.Txn.TICKER, "MSFT")(txn_id)
    shutil.copy2(backup, db_path)


@pytest.mark.parametrize(
    ("change", "replays"),
    [
        pytest.param(lambda *_: None, False, id="nothing changed"),
        pytest.param(_write_quote, False, id="quotes refreshed"),
        pytest.param(
            _configure("quotes", {"ttl_minutes": 60}),
            False,
            id="config outside accounts",
        ),
        pytest.param(
            lambda *_: seed_fx({"2025-08-19": "1.3800"}),
            False,
            id="FX rate after the cutoff",
        ),
        pytest.param(_restore_backup_over_an_edit, False, id="backup restored"),
        pytest.param(
            _set(Column.Txn.SETTLE_DATE, "2025-08-15"),
            True,
            id="settlement date",
        ),
        pytest.param(_add_transaction, True, id="transaction added"),
        pytest.param(_delete_transaction, True, id="transaction deleted"),
        pytest.param(_set(Column.Txn.TICKER, "MSFT"), True, id="ticker"),
        pytest.param(_set(Column.Txn.ACCOUNT, "IBKR-TFSA"), True, id="account"),
        pytest.param(_add_alias, True, id="alias added"),
        pytest.param(
            _configure(
                "accounts",
                {"map": {"IBKR-PERSONAL": {"amount_includes_fees": "false"}}},
            ),
            True,
            id="per-account fee override",
        ),
        pytest.param(
            lambda *_: seed_fx({CUTOFF: "1.3700"}),
            True,
            id="FX rate on the cutoff",
        ),
        pytest.param(
            lambda *_: seed_fx({"2025-08-15": "1.3600"}),
            True,
            id="FX gap filled before the cutoff",
        ),
    ],
)
def test_the_saved_ledger_is_reused_until_a_replay_input_changes(
    saved_txn_id: int,
    change: Change,
    *,
    replays: bool,
) -> None:
    change(saved_txn_id)
    assert _replays() is replays


@pytest.mark.real_saved_ledger
def test_a_ledger_saved_before_change_tokens_replays_once(
    temp_ctx: TempContext,
) -> None:
    """A folio from before change tokens has no triggers, tokens or new meta."""
    with temp_ctx() as ctx:
        seed_cad_holding()
        assert _replays()
        assert not _replays()

        meta_path = ctx.config.acb_parquet.with_suffix(".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["fingerprint"] = meta.pop("inputs")
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        with get_connection() as conn:
            for trigger in get_triggers(conn):
                conn.execute(f'DROP TRIGGER "{trigger}"')
            drop_table(conn, Table.REVISIONS)
            # Such a folio is from before migrations too.
            conn.execute("PRAGMA user_version = 0")

        assert _replays()
        assert not _replays()
        # The migration put the triggers back, so an edit is seen again.
        seed_cad_holding()
        assert _replays()


def test_refresh_replays_a_current_saved_ledger(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_cad_holding()
        assert _replays()
        assert not _replays()
        assert open_ledger(refresh=True, offline=True).computed_at is None


def _saved_files(ctx: AppContext) -> list[str]:
    """Name every file the saved ledger is kept in."""
    return sorted(path.name for path in ctx.config.acb_parquet.parent.glob("acb*"))


@pytest.mark.real_saved_ledger
def test_each_save_removes_the_files_no_meta_names(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        # A ledger saved in an older format.
        ctx.config.acb_parquet.write_bytes(b"")
        seed_cad_holding()
        assert _replays()
        assert open_ledger(refresh=True, offline=True).computed_at is None

        meta = json.loads(ctx.config.acb_parquet.with_suffix(".meta.json").read_text())
        save = meta["save"]
        assert _saved_files(ctx) == [
            f"acb.{save}.diagnostics.parquet",
            f"acb.{save}.parquet",
            "acb.meta.json",
        ]


@pytest.mark.real_saved_ledger
def test_a_meta_naming_removed_files_replays(temp_ctx: TempContext) -> None:
    """Two saves at once: the meta landing last may name files the other removed."""
    with temp_ctx() as ctx:
        seed_cad_holding()
        assert _replays()
        meta_path = ctx.config.acb_parquet.with_suffix(".meta.json")
        first = meta_path.read_text(encoding="utf-8")
        assert open_ledger(refresh=True, offline=True).computed_at is None

        meta_path.write_text(first, encoding="utf-8")
        assert _replays()
        assert not _replays()


@pytest.mark.real_saved_ledger
def test_an_undecodable_saved_ledger_replays(temp_ctx: TempContext) -> None:
    """A value no release knows, in a meta whose inputs still match."""
    with temp_ctx() as ctx:
        seed_cad_holding()
        assert _replays()
        meta_path = ctx.config.acb_parquet.with_suffix(".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["cash"][0]["grain"] = "NO_SUCH_GRAIN"
        meta_path.write_text(json.dumps(meta), encoding="utf-8")

        assert _replays()
        assert not _replays()


@pytest.mark.real_saved_ledger
def test_an_interrupted_save_leaves_the_last_whole_one(temp_ctx: TempContext) -> None:
    """The save stopped part way: its files go, and the one before still stands."""
    with temp_ctx() as ctx:
        seed_cad_holding()
        assert _replays()
        files = _saved_files(ctx)
        computed_at = open_ledger(offline=True).computed_at
        assert computed_at is not None

        write = pd.DataFrame.to_parquet
        written: list[object] = []

        def interrupted(frame: pd.DataFrame, *args: object, **kwargs: object) -> None:
            if written:
                raise KeyboardInterrupt
            written.append(write(frame, *args, **kwargs))  # ty: ignore[no-matching-overload]

        with (
            patch.object(pd.DataFrame, "to_parquet", interrupted),
            pytest.raises(KeyboardInterrupt),
        ):
            open_ledger(refresh=True, offline=True)

        assert written
        assert _saved_files(ctx) == files
        assert open_ledger(offline=True).computed_at == computed_at


# --- folio symbol --------------------------------------------------------------


def test_symbol_list_before_any_alias(temp_ctx: TempContext) -> None:
    """Test --list when the alias table has never been created."""
    with temp_ctx() as ctx:
        seed_transaction()
        cli_result = run_cli_with_config(ctx.config, app, ["symbol", "--list"])
        assert_cli_success(cli_result)
        assert_in_output("No ticker aliases found.", cli_result)


def test_symbol_delete_before_any_alias(temp_ctx: TempContext) -> None:
    """Test --delete when the alias table has never been created."""
    with temp_ctx() as ctx:
        seed_transaction()
        cli_result = run_cli_with_config(
            ctx.config,
            app,
            ["symbol", "--delete", "OLD"],
        )
        assert_cli_success(cli_result)
        assert_in_output("Alias for 'OLD' not found.", cli_result)


def test_symbol_command(temp_ctx: TempContext) -> None:
    """Add, list and delete ticker aliases with the symbol command."""
    with temp_ctx() as ctx:
        config = ctx.config
        # 1. ADD
        cli_result = run_cli_with_config(
            config,
            app,
            ["symbol", "--add", "OLD", "NEW", "2025-01-01"],
        )
        assert_cli_success(cli_result)
        assert_in_output("Successfully added alias.", cli_result)
        with get_connection() as conn:
            count = get_row_count(conn, Table.TICKER_ALIASES)
            assert count == 1

        # 2. ADD another alias
        cli_result = run_cli_with_config(
            config,
            app,
            ["symbol", "--add", "NEW", "NEWEST", "2025-02-01"],
        )
        assert_cli_success(cli_result)

        # 3. LIST aliases
        cli_result = run_cli_with_config(config, app, ["symbol", "--list"])
        assert_cli_success(cli_result)
        assert_in_output("OLD", cli_result)
        assert_in_output("NEW", cli_result)
        assert_in_output("NEWEST", cli_result)

        # 4. DELETE an alias
        cli_result = run_cli_with_config(
            config,
            app,
            ["symbol", "--delete", "OLD"],
        )
        assert_cli_success(cli_result)
        assert_in_output("Successfully deleted alias", cli_result)
        with get_connection() as conn:
            count = get_row_count(conn, Table.TICKER_ALIASES)
            assert count == 1
            remaining = get_rows(conn, Table.TICKER_ALIASES)
            assert remaining.iloc[0][Column.Aliases.OLD_TICKER] == "NEW"

        # 5. LIST once the last alias is gone
        run_cli_with_config(config, app, ["symbol", "--delete", "NEW"])
        cli_result = run_cli_with_config(config, app, ["symbol", "--list"])
        assert_in_output("No ticker aliases found.", cli_result)


def test_acb_resolves_a_renamed_symbol(temp_ctx: TempContext) -> None:
    """Rows written before the rename pool under the new name."""
    with temp_ctx() as ctx:
        seed_fx(FX)
        seed_transaction(
            action="BUY",
            date="2025-08-14",
            settle_date="2025-08-14",
            account="IBKR-PERSONAL",
            currency="CAD",
            ticker="SPLG",
            amount="-1000",
            price="10",
            units="100",
        )
        run_cli_with_config(
            ctx.config,
            app,
            ["symbol", "--add", "SPLG", "SPYM", "2025-10-31"],
        )
        result = run_cli_with_config(ctx.config, app, ["acb", "SPLG", "--refresh"])
    assert_cli_success(result)
    assert_in_output("SPYM", result)


def test_acb_export_to_parquet(temp_ctx: TempContext, tmp_path: Path) -> None:
    target = tmp_path / "acb_export.parquet"
    with temp_ctx() as ctx:
        seed_cad_holding()
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "RY.TO", "--export", str(target)],
        )
        assert_cli_success(result)
        assert target.exists()


def test_acb_prices_only_the_buys_it_shows(
    temp_ctx: TempContext,
    tmp_path: Path,
    quote_provider: FakeQuoteProvider,
) -> None:
    target = tmp_path / "shown.parquet"
    with temp_ctx() as ctx:
        seed_usd_holding(ticker=TICKER)
        seed_usd_holding(ticker="OTHER")
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", TICKER, "--export", str(target)],
        )

        assert_cli_success(result)
        asked = {s for batch in quote_provider.asked("prices") for s in batch}
        assert asked == {TICKER}
        # Ten units bought for 1,000 USD, quoted at 200.
        assert pd.read_parquet(target)["AcctGain_USD"].tolist() == [1000]


def test_acb_offline_asks_for_no_quotes_and_leaves_a_buy_blank(
    temp_ctx: TempContext,
    tmp_path: Path,
    quote_provider: FakeQuoteProvider,
) -> None:
    target = tmp_path / "offline.parquet"
    with temp_ctx() as ctx:
        seed_usd_holding(ticker=TICKER)
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", TICKER, "--offline", "--export", str(target)],
        )

        assert_cli_success(result)
        assert not quote_provider.calls
        assert pd.read_parquet(target)["AcctGain_USD"].isna().all()


def test_acb_summary_asks_for_no_quotes(
    temp_ctx: TempContext,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx() as ctx:
        seed_usd_holding(ticker=TICKER)
        result = run_cli_with_config(ctx.config, app, ["acb", "--summary"])

    assert_cli_success(result)
    assert not quote_provider.calls


def test_acb_on_an_empty_folio(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        result = run_cli_with_config(ctx.config, app, ["acb", "RY.TO"])
    assert_cli_success(result)
    assert_in_output("No transactions", result)


def test_acb_summary_with_no_holdings(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_fx(FX)
        seed_cash(
            "CONTRIBUTION",
            amount="5000",
            date="2025-08-14",
            settle_date="2025-08-14",
            account="IBKR-PERSONAL",
        )
        result = run_cli_with_config(ctx.config, app, ["acb", "--summary"])
    assert_cli_success(result)
    assert_in_output("No holdings", result)


# --- table presentation -----------------------------------------------------------
#
# Display only: the replay still walks transaction dates, and none of these change a
# reported number.


def test_a_parquet_export_never_refuses_a_currency_choice(
    temp_ctx: TempContext,
    tmp_path: Path,
) -> None:
    """The file is for another program, which takes every currency's figures."""
    target = tmp_path / "usd.parquet"
    with temp_ctx() as ctx:
        seed_cad_holding()
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "RY.TO", "--currency", "USD", "--export", str(target)],
        )
        assert_cli_success(result)
        assert len(pd.read_parquet(target)) == 2


def test_a_parquet_summary_holds_one_row_per_symbol(
    temp_ctx: TempContext,
    tmp_path: Path,
) -> None:
    target = tmp_path / "summary.parquet"
    with temp_ctx() as ctx:
        seed_cad_holding()
        seed_cad_holding(ticker="TSTKR.TO")
        result = run_cli_with_config(
            ctx.config,
            app,
            ["acb", "--summary", "--export", str(target)],
        )
        assert_cli_success(result)
        summary = pd.read_parquet(target)
    assert summary["Symbol"].tolist() == ["RY.TO", "TSTKR.TO"]
    assert Column.Txn.TXN_ID not in summary.columns
