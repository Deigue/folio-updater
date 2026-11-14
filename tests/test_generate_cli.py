"""Tests for `folio generate`: the whole folio, written as one workbook."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, NoReturn

import pandas as pd
import pytest
from openpyxl import load_workbook
from pandas.testing import assert_frame_equal

from cli.main import app
from datagen import ensure_data_exists
from db import aliases, drop_table, get_columns, get_connection, get_row_count
from db.backup import backup_file
from domain import Column, Table
from engine.ledger import LedgerUnavailableError
from exporters.excel_writer import sheet_name
from importers.excel_importer import import_transactions
from models import TickerAlias

from .helpers.cli import assert_cli_success, assert_in_output, run_cli
from .helpers.seed import seed_transaction, seed_two_account_types

if TYPE_CHECKING:
    from pathlib import Path

    from .fixtures.fake_quote_provider import FakeQuoteProvider
    from .helpers.cli import CliTestResult
    from .helpers.context import TempContext

# Every sheet a folio holding in two account types, in two accounts, gets.
EVERY_SHEET = [
    "Summary",
    "Flows",
    "Performance",
    "Portfolio",
    "NON-REGISTERED",
    "TFSA",
    "WS-PERSONAL",
    "WS-TFSA",
    "Ledger",
    "Cost Base",
    "Txns",
    "FX",
    "Tickers",
]


def _generate(target: Path, *extra: str) -> CliTestResult:
    """Run `folio generate` into `target`, with any extra arguments."""
    return run_cli(
        app,
        ["generate", "--out", str(target), *extra],
    )


def test_generate_writes_a_sheet_for_every_view_of_the_folio(
    temp_ctx: TempContext,
    tmp_path: Path,
) -> None:
    """Only pools that hold something get a dashboard: no RRSP sheet here."""
    with temp_ctx():
        seed_two_account_types()
        target = tmp_path / "folio.xlsx"

        result = _generate(target)

        assert_cli_success(result)
        assert load_workbook(target).sheetnames == EVERY_SHEET


def test_the_ledger_sheet_prices_buys_of_closed_holdings_too(
    temp_ctx: TempContext,
    tmp_path: Path,
    quote_provider: FakeQuoteProvider,
) -> None:
    with temp_ctx():
        seed_transaction()  # Something open, so the folio has a dashboard to value.
        seed_transaction(ticker="OTHER", amount="-1000", price="100", units="10")
        seed_transaction(
            action="SELL",
            ticker="OTHER",
            amount="1200",
            price="120",
            units="-10",
        )
        target = tmp_path / "folio.xlsx"

        result = _generate(target, "--only", "acb")

        assert_cli_success(result)
        asked = {s for batch in quote_provider.asked("prices") for s in batch}
        assert "OTHER" in asked
        rows = list(load_workbook(target)["Ledger"].iter_rows(values_only=True))
        header = next(row for row in rows if "Acct Gain USD" in row)
        named = [dict(zip(header, row, strict=True)) for row in rows]
        bought = next(
            row for row in named if row["Symbol"] == "OTHER" and row["Action"] == "BUY"
        )
        # Ten units bought for 1,000 USD, quoted at 50.
        assert bought["Acct Gain USD"] == pytest.approx(-500)


def test_the_txns_sheet_imports_back_into_an_empty_folio(
    temp_ctx: TempContext,
    tmp_path: Path,
) -> None:
    """The workbook is a backup a folio can be rebuilt from.

    The import pipeline grafts any column it does not recognise onto the
    database, so a Txns sheet carrying a computed column would widen the table
    the first time it came back in. It must carry exactly what was stored.
    """
    with temp_ctx() as ctx:
        seed_two_account_types()
        target = tmp_path / "folio.xlsx"
        assert_cli_success(_generate(target))

        with get_connection() as conn:
            stored = set(get_columns(conn, Table.TXNS))
            count = get_row_count(conn, Table.TXNS)
            drop_table(conn, Table.TXNS)

        # Out of the tab strip, since the Ledger shows a reader every column,
        # but still found by name.
        assert load_workbook(target)[ctx.config.txn_sheet].sheet_state == "hidden"
        import_transactions(target, None, ctx.config.txn_sheet)

        with get_connection() as conn:
            assert set(get_columns(conn, Table.TXNS)) == stored
            assert get_row_count(conn, Table.TXNS) == count


def test_only_narrows_the_workbook_to_the_sections_named(
    temp_ctx: TempContext,
    tmp_path: Path,
) -> None:
    with temp_ctx():
        seed_two_account_types()
        target = tmp_path / "folio.xlsx"

        # Spacing and a stray trailing comma are forgiven.
        result = _generate(target, "--only", "acb, summary,")

        assert_cli_success(result)
        assert load_workbook(target).sheetnames == [
            "Summary",
            "Flows",
            "Ledger",
            "Cost Base",
        ]


def test_only_stored_writes_just_the_stored_sheets(
    temp_ctx: TempContext,
    tmp_path: Path,
) -> None:
    with temp_ctx():
        seed_two_account_types()
        target = tmp_path / "folio.xlsx"

        result = _generate(target, "--only", "stored")

        assert_cli_success(result)
        assert load_workbook(target).sheetnames == ["Txns", "FX", "Tickers"]


def test_generating_over_the_folio_keeps_a_backup_of_the_last_one(
    temp_ctx: TempContext,
) -> None:
    """The configured folio is rotated into its backups before it is overwritten."""
    backup = {"enabled": True, "path": "backups", "max_backups": 5}
    with temp_ctx(backup=backup) as ctx:
        seed_two_account_types()
        config = ctx.config

        for _ in range(2):
            assert_cli_success(run_cli(app, ["generate"]))

        kept = list((config.backup_path / "folio_xlsx").glob("*.xlsx"))
        assert len(kept) == 1


def test_rotation_keeps_the_newest_backups_by_name_not_by_modified_time(
    temp_ctx: TempContext,
) -> None:
    """A folio restored by hand has an old modified time, yet its backup is newest."""
    backup = {"enabled": True, "path": "backups", "max_backups": 2}
    with temp_ctx(backup=backup) as ctx:
        config = ctx.config
        config.folio_path.parent.mkdir(parents=True, exist_ok=True)
        config.folio_path.write_bytes(b"restored")
        os.utime(config.folio_path, (0, 0))
        kept_dir = config.backup_path / "folio_xlsx"
        kept_dir.mkdir(parents=True)
        for stamp in ("20200101_000000", "20200102_000000"):
            (kept_dir / f"folio_{stamp}.xlsx").write_bytes(b"earlier")

        backup_file(config.folio_path)

        names = sorted(path.name for path in kept_dir.glob("*.xlsx"))
        assert len(names) == 2
        assert names[0] == "folio_20200102_000000.xlsx"
        assert names[1] > names[0]  # the backup just taken


def test_only_refuses_a_section_that_does_not_exist(
    temp_ctx: TempContext,
    tmp_path: Path,
) -> None:
    with temp_ctx():
        seed_two_account_types()
        target = tmp_path / "folio.xlsx"

        result = _generate(target, "--only", "dashboards")

        assert result.exit_code == 1
        assert_in_output("summary, perf, dash, accounts, acb, stored", result)
        assert not target.exists()


def test_generate_refuses_a_csv(temp_ctx: TempContext, tmp_path: Path) -> None:
    """A workbook of a dozen sheets has nowhere to go in one table."""
    with temp_ctx():
        seed_two_account_types()
        target = tmp_path / "folio.csv"

        result = _generate(target)

        assert result.exit_code == 1
        assert_in_output("A CSV holds one table", result)
        assert not target.exists()


def test_sheet_names_differing_only_in_case_are_told_apart() -> None:
    """Excel matches sheet names without case, so the second would be the first."""
    renamed = sheet_name("WS-TFSA", ["WS-tfsa"])

    assert renamed.casefold() != "ws-tfsa"


def test_an_unreplayable_folio_still_writes_its_stored_sheets(
    temp_ctx: TempContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`generate` wrote the transactions before it could value anything.

    A folio the engine cannot replay must not lose that: it gets the stored
    sheets, and a warning saying why the rest are missing.
    """

    def unreplayable(**_kwargs: object) -> NoReturn:
        msg = "FX rates are empty, so USD cannot be converted."
        raise LedgerUnavailableError(msg)

    with temp_ctx():
        seed_two_account_types()
        monkeypatch.setattr("cli.commands.generate.open_ledger", unreplayable)
        target = tmp_path / "folio.xlsx"

        result = _generate(target)

        assert_cli_success(result)
        assert_in_output("only the stored", result)
        assert load_workbook(target).sheetnames == ["Txns", "FX", "Tickers"]


def test_an_empty_folio_writes_nothing(temp_ctx: TempContext, tmp_path: Path) -> None:
    with temp_ctx():
        target = tmp_path / "folio.xlsx"

        result = _generate(target)

        assert_cli_success(result)
        assert_in_output("No transactions", result)
        assert not target.exists()


def test_a_ticker_reused_after_its_rename_gets_a_row_per_security(
    temp_ctx: TempContext,
    tmp_path: Path,
) -> None:
    with temp_ctx() as ctx:
        seed_transaction(ticker="OLDCO", currency="CAD", date="2025-08-14")
        renamed = TickerAlias(
            old_ticker="OLDCO",
            new_ticker="NEWCO",
            effective_date="2025-08-15",
        )
        aliases.write([renamed])
        # A new listing under the old name, after the rename.
        seed_transaction(ticker="OLDCO", currency="CAD", date="2025-08-18")
        target = tmp_path / "folio.xlsx"

        assert_cli_success(_generate(target))
        sheet = pd.read_excel(target, sheet_name=ctx.config.tickers_sheet, header=2)

    pairs = set(zip(sheet[Column.Ticker.TICKER], sheet["Symbol"], strict=True))
    assert pairs == {("OLDCO", "NEWCO"), ("OLDCO", "OLDCO")}


def test_generate_command(temp_ctx: TempContext) -> None:
    """The workbook's source sheets hold what the database holds, and nothing more."""
    with temp_ctx() as ctx:
        config = ctx.config
        ensure_data_exists()
        assert config.txn_parquet.exists()
        assert config.tickers_parquet.exists()
        assert not config.folio_path.exists()
        cli_result = run_cli(app, ["generate"])
        assert_cli_success(cli_result)
        assert_in_output("sheets to", cli_result)
        assert config.folio_path.exists()
        transactions_parquet = pd.read_parquet(config.txn_parquet, engine="pyarrow")
        tickers_parquet = pd.read_parquet(config.tickers_parquet, engine="pyarrow")
        transactions_excel = pd.read_excel(
            config.folio_path,
            sheet_name=config.txn_sheet,
        )
        # A column no row fills reads back from Excel as numbers, whatever it holds.
        transactions_excel[Column.Txn.DESCRIPTION] = transactions_excel[
            Column.Txn.DESCRIPTION
        ].astype(object)
        # The link back to the Summary, then the column bands, then the header.
        tickers_excel = pd.read_excel(
            config.folio_path,
            sheet_name=config.tickers_sheet,
            header=2,
        )
        assert_frame_equal(
            transactions_parquet.reset_index(drop=True).fillna(pd.NA),
            transactions_excel.reset_index(drop=True).fillna(pd.NA),
        )
        # The tickers sheet now carries what the quote cache knows of each one
        # too, and keeps a renamed security's names together, but holds the same
        # tickers.
        assert sorted(tickers_parquet[Column.Ticker.TICKER]) == sorted(
            tickers_excel[Column.Ticker.TICKER],
        )
