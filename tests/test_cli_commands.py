"""Tests for CLI commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable
from unittest.mock import patch

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from typer.testing import CliRunner

from cli.commands import import_cmd
from cli.main import app as cli_app
from db import db
from mock.folio_setup import ensure_data_exists
from tests.fixtures.dataframe_cache import register_test_dataframe
from utils.constants import Column, Table

from .fixtures.test_data_factory import create_transaction_data

if TYPE_CHECKING:
    from click.testing import Result
    from typer import Typer

    from utils.config import Config

    from .test_types import TempContext

runner = CliRunner()

EXPECTED_TRANSACTION_COUNT = 2
TYPER_INVALID_COMMAND_EXIT_CODE = 2


@pytest.fixture(autouse=True)
def suppress_logging_conflicts() -> db.Generator[None, Any, None]:
    """Suppress logging to avoid stream conflicts during CLI testing."""
    # Temporarily disable all loggers to avoid stream conflicts
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


def test_demo_command(temp_ctx: TempContext) -> None:
    """Test demo command through main CLI app."""
    with temp_ctx() as ctx:
        config = ctx.config
        assert not config.txn_parquet.exists()
        assert not config.tkr_parquet.exists()
        # * forex tested separately
        result = _run_cli_with_config(config, cli_app, ["demo"])
        assert_cli_success(result)
        assert "Demo portfolio created successfully!" in result.stdout
        assert config.txn_parquet.exists()
        assert config.tkr_parquet.exists()


@pytest.mark.no_mock_forex
def test_getfx_command(
    temp_ctx: TempContext,
    cached_fx_data: Callable[[str | None], pd.DataFrame],
) -> None:
    """Test getfx command through main CLI app."""
    with temp_ctx() as ctx:
        config = ctx.config
        ensure_data_exists()

        # Drop the FX rates table to force fresh fetch
        with db.get_connection() as conn:
            db.drop_table(conn, Table.FX)

        # Use cached FX data instead of real API call
        with patch(
            "services.forex_service.ForexService.get_fx_rates_from_boc",
        ) as mock_fx:
            mock_fx.return_value = cached_fx_data(None)
            result = _run_cli_with_config(config, cli_app, ["getfx"])
            assert_cli_success(result)
            assert "Successfully updated" in result.stdout
            with db.get_connection() as conn:
                count = db.get_row_count(conn, Table.FX)
                assert count > 0


def test_import_command(temp_ctx: TempContext) -> None:
    """Test import command default behavior (import from configured folio file)."""
    with temp_ctx() as ctx:
        config = ctx.config
        create_transaction_data(config.folio_path, config.txn_sheet)
        result = _run_cli_with_config(config, cli_app, ["import"])
        assert_cli_success(result)
        assert (
            f"Successfully imported {EXPECTED_TRANSACTION_COUNT} transactions"
            in result.stdout
        )
        with db.get_connection() as conn:
            count = db.get_row_count(conn, Table.TXNS)
            assert count == EXPECTED_TRANSACTION_COUNT


def test_import_command_missing_folio(temp_ctx: TempContext) -> None:
    """Test import command when folio file doesn't exist."""
    with temp_ctx() as ctx:
        config = ctx.config
        assert not config.folio_path.exists()
        result = _run_cli_with_config(config, import_cmd.app)
        assert result.exit_code == 1
        assert f"Folio file not found: {config.folio_path}" in result.stderr


def test_import_command_file(temp_ctx: TempContext) -> None:
    """Test import command with specific file option."""
    with temp_ctx() as ctx:
        config = ctx.config
        test_file = config.project_root / "test_import.xlsx"
        create_transaction_data(test_file)
        result = _run_cli_with_config(
            config,
            import_cmd.app,
            ["--file", str(test_file)],
        )
        assert_cli_success(result)
        # Verify via stdout keywords, core functionality is tested elsewhere.
        assert f"Importing {test_file.name}..." in result.stdout
        assert (
            f"Successfully imported {EXPECTED_TRANSACTION_COUNT} transactions "
            f"from {test_file.name}" in result.stdout
        )
        assert (
            f"Exported {EXPECTED_TRANSACTION_COUNT} transactions to Parquet"
            in result.stdout
        )
        processed_folder = config.processed_path
        assert processed_folder.exists()
        assert (processed_folder / test_file.name).exists()
        assert not test_file.exists()


def test_import_command_directory(temp_ctx: TempContext) -> None:
    """Test import command with directory option."""
    with temp_ctx() as ctx:
        config = ctx.config
        import_dir = config.imports_path
        file1 = import_dir / "transactions1.xlsx"
        file2 = import_dir / "transactions2.xlsx"
        create_transaction_data(file1)
        create_transaction_data(file2)
        ensure_data_exists()
        result = _run_cli_with_config(
            config,
            import_cmd.app,
            ["--dir", str(import_dir)],
        )
        assert_cli_success(result)
        assert "Found 2 files to import" in result.stdout
        assert "Total transactions imported: 4" in result.stdout
        assert "Exported 54 transactions to Parquet" in result.stdout
        processed_folder = config.processed_path
        assert processed_folder.exists()
        assert (processed_folder / file1.name).exists()
        assert (processed_folder / file2.name).exists()
        assert not file1.exists()
        assert not file2.exists()


def test_generate_command(temp_ctx: TempContext) -> None:
    """Test generate command creates Excel from Parquet files."""
    with temp_ctx() as ctx:
        config = ctx.config
        ensure_data_exists()
        assert config.txn_parquet.exists()
        assert config.tkr_parquet.exists()
        assert not config.folio_path.exists()
        result = _run_cli_with_config(config, cli_app, ["generate"])
        assert_cli_success(result)
        assert "Excel workbook generated successfully" in result.stdout
        assert config.folio_path.exists()
        transactions_parquet = pd.read_parquet(config.txn_parquet, engine="pyarrow")
        tickers_parquet = pd.read_parquet(config.tkr_parquet, engine="pyarrow")
        transactions_excel = pd.read_excel(
            config.folio_path,
            sheet_name=config.txn_sheet,
        )
        tickers_excel = pd.read_excel(
            config.folio_path,
            sheet_name=config.tkr_sheet,
        )
        assert_frame_equal(
            transactions_parquet.reset_index(drop=True).fillna(pd.NA),
            transactions_excel.reset_index(drop=True).fillna(pd.NA),
        )
        assert_frame_equal(
            tickers_parquet.reset_index(drop=True).fillna(pd.NA),
            tickers_excel.reset_index(drop=True).fillna(pd.NA),
        )


def test_settle_info_command(temp_ctx: TempContext) -> None:
    """Test settle info command output."""
    with temp_ctx() as ctx:
        ensure_data_exists()
        with db.get_connection() as conn:
            # Count transactions with SETTLE_CALCULATED = 1, this should represent
            # the total transactions that were auto-calculated.
            calculated_count = db.get_row_count(
                conn,
                Table.TXNS,
                condition=f'"{Column.Txn.SETTLE_CALCULATED}" = 1',
            )
        result = _run_cli_with_config(ctx.config, cli_app, ["settle-info"])
        assert f"Calculated settlement dates: {calculated_count}" in result.stdout
        assert_cli_success(result)


def test_settle_info_with_file_import(temp_ctx: TempContext) -> None:
    """Test settle info command with file import."""
    with temp_ctx() as ctx:
        ensure_data_exists()
        statement_df = pd.DataFrame(
            [
                {
                    "date": "2025-07-25",
                    "amount": 17995.355,
                    "currency": "USD",
                    "transaction": "BUY",
                    "description": "SPY - 94.34 SHARES 2025-07-23",
                },
            ],
        )

        statement_file = ctx.config.project_root / "test_statement.xlsx"
        register_test_dataframe(statement_file, statement_df)
        result = _run_cli_with_config(
            ctx.config,
            cli_app,
            [
                "settle-info",
                "-f",
                str(statement_file),
            ],
        )
        assert "Successfully updated 1 settlement dates." in result.stdout
        assert_cli_success(result)


def test_settle_info_with_nonexistent_file(temp_ctx: TempContext) -> None:
    """Test settle info command with nonexistent file."""
    with temp_ctx() as ctx:
        nonexistent_file = ctx.config.project_root / "does_not_exist.xlsx"

        result = _run_cli_with_config(
            ctx.config,
            cli_app,
            ["settle-info", "-f", str(nonexistent_file)],
        )
        assert "does not exist" in result.stderr
        assert result.exit_code == 1


def test_version_command() -> None:
    """Test version command output."""
    result = runner.invoke(cli_app, ["version"])

    assert_cli_success(result)
    assert "folio-updater version:" in result.stdout


def _run_cli_with_config(
    config: Config,
    command_app: Typer,
    args: list[str] | None = None,
) -> Result:
    """Run CLI commands with proper config mocking."""
    if args is None:
        args = []

    # Mock bootstrap.reload_config to return our test config
    with patch("app.bootstrap.reload_config") as mock_reload:
        mock_reload.return_value = config
        return runner.invoke(command_app, args)


def assert_cli_success(result: Result) -> None:  # pragma: no cover
    if result.exit_code != 0:
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        if result.exception:
            print("EXCEPTION:", repr(result.exception))
    assert result.exit_code == 0, (
        f"CLI failed: {result.exit_code}\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}\n"
        f"EXCEPTION:\n{result.exception!r}"
    )
