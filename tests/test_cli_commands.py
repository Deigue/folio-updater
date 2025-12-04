"""Tests for CLI commands."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from rich.progress import Progress
from typer.testing import CliRunner

from cli import console as console_module
from cli.commands import import_cmd
from cli.commands.download import _resolve_from_date
from cli.main import app as cli_app
from cli.test_console import capture_output
from datagen import DEFAULT_TXN_COUNT, ensure_data_exists, generate_transactions
from db import drop_table, get_connection, get_row_count, get_rows
from services.ibkr_service import IBKRAuthenticationError
from tests.fixtures.dataframe_cache import register_test_dataframe
from tests.fixtures.ibkr_mocking import (
    IBKRMockContext,
    get_default_mock_csv,
)
from tests.fixtures.wealthsimple_mocking import (
    WealthsimpleMockContext,
    get_expected_statement_txn_csv,
    get_expected_wealthsimple_csv,
    get_mock_activities,
    get_mock_statement_transactions,
)
from utils.constants import DEFAULT_TICKERS, Column, Table

from .fixtures.test_data_factory import create_transaction_data

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from typer import Typer

    from utils.config import Config

    from .test_types import TempContext

runner = CliRunner()

EXPECTED_TRANSACTION_COUNT = 2
TYPER_INVALID_COMMAND_EXIT_CODE = 2


@dataclass
class CliTestResult:
    """Dataclass to hold flattened CLI test results for cleaner access."""

    exit_code: int
    stdout: str
    stderr: str | None
    exception: BaseException | None
    plain_output: str


@pytest.fixture(autouse=True)
def suppress_logging_conflicts() -> Generator[None, Any]:
    """Suppress logging to avoid stream conflicts during CLI testing."""
    # Temporarily disable all loggers to avoid stream conflicts
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


@pytest.fixture(autouse=True)
def patch_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture to patch Rich Progress to be non-transient and use test console."""
    original_init = Progress.__init__

    def new_init(self: Progress, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        kwargs["console"] = console_module.console
        kwargs["transient"] = False  # Ensure progress bars are not cleared
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(Progress, "__init__", new_init)


def test_demo_command(temp_ctx: TempContext) -> None:
    """Test demo command through main CLI app."""
    with temp_ctx() as ctx:
        config = ctx.config
        assert not config.txn_parquet.exists()
        assert not config.tkr_parquet.exists()
        # * forex tested separately
        cli_result = _run_cli_with_config(config, cli_app, ["demo"])
        assert_cli_success(cli_result)
        assert_in_output("Demo portfolio created successfully!", cli_result)
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
        with get_connection() as conn:
            drop_table(conn, Table.FX)

        # Use cached FX data instead of real API call
        with patch(
            "services.forex_service.ForexService.get_fx_rates_from_boc",
        ) as mock_fx:
            mock_fx.return_value = cached_fx_data(None)
            cli_result = _run_cli_with_config(config, cli_app, ["getfx"])
            assert_cli_success(cli_result)
            assert_in_output("Successfully updated", cli_result)
            with get_connection() as conn:
                count = get_row_count(conn, Table.FX)
                assert count > 0


def test_import_command(temp_ctx: TempContext) -> None:
    """Test import command default behavior."""
    with temp_ctx() as ctx:
        config = ctx.config
        txn_file = config.imports_path / "transactions.xlsx"
        create_transaction_data(txn_file, config.txn_sheet)
        cli_result = _run_cli_with_config(config, cli_app, ["import"])
        assert_cli_success(cli_result)
        assert_in_output(
            f"{EXPECTED_TRANSACTION_COUNT} transactions imported",
            cli_result,
        )
        with get_connection() as conn:
            count = get_row_count(conn, Table.TXNS)
            assert count == EXPECTED_TRANSACTION_COUNT


def test_import_command_missing_folio(temp_ctx: TempContext) -> None:
    """Test import command when files don't exist."""
    with temp_ctx() as ctx:
        config = ctx.config
        assert not config.folio_path.exists()
        cli_result = _run_cli_with_config(config, import_cmd.app)
        assert cli_result.exit_code == 1
        assert_in_output("No supported files found", cli_result)


def test_import_command_file(temp_ctx: TempContext) -> None:
    """Test import command with specific file option."""
    with temp_ctx() as ctx:
        config = ctx.config
        test_file = config.project_root / "test_import.xlsx"
        create_transaction_data(test_file)
        cli_result = _run_cli_with_config(
            config,
            import_cmd.app,
            ["--file", str(test_file)],
        )
        assert_cli_success(cli_result)
        # Verify via stdout keywords, core functionality is tested elsewhere.
        assert_in_output(f"SUCCESS: {test_file.name}", cli_result)
        assert_in_output(
            f"{EXPECTED_TRANSACTION_COUNT} transactions imported",
            cli_result,
        )
        assert_in_output(
            f"Exported {EXPECTED_TRANSACTION_COUNT} transactions to Parquet",
            cli_result,
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
        file_count: int = 2
        create_transaction_data(file1)
        create_transaction_data(file2)
        ensure_data_exists()
        cli_result = _run_cli_with_config(
            config,
            import_cmd.app,
            ["--dir", str(import_dir)],
        )
        assert_cli_success(cli_result)
        assert_in_output("Found 2 files to import", cli_result)
        txn_count = EXPECTED_TRANSACTION_COUNT * file_count
        assert_in_output(f"Total transactions imported: {txn_count}", cli_result)
        mock_txn_count = DEFAULT_TXN_COUNT * len(DEFAULT_TICKERS)
        total_txns = mock_txn_count + txn_count
        assert_in_output(f"Exported {total_txns} transactions to Parquet", cli_result)
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
        cli_result = _run_cli_with_config(config, cli_app, ["generate"])
        assert_cli_success(cli_result)
        assert_in_output("Excel workbook generated successfully", cli_result)
        assert config.folio_path.exists()
        transactions_parquet = pd.read_parquet(config.txn_parquet, engine="fastparquet")
        tickers_parquet = pd.read_parquet(config.tkr_parquet, engine="fastparquet")
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
        with get_connection() as conn:
            # Count transactions with SETTLE_CALCULATED = 1, this should represent
            # the total transactions that were auto-calculated.
            calculated_count = get_row_count(
                conn,
                Table.TXNS,
                condition=f'"{Column.Txn.SETTLE_CALCULATED}" = 1',
            )
        cli_result = _run_cli_with_config(ctx.config, cli_app, ["settle-info"])
        assert_in_output(
            f"Calculated settlement dates: {calculated_count}",
            cli_result,
        )
        assert_cli_success(cli_result)


@pytest.mark.parametrize(
    ("cli_args", "expected_output", "setup_type"),
    [
        (
            ["--import", "-f", "test_statement.xlsx"],
            'Updated 1 settlement dates from "test_statement.xlsx"',
            "file",
        ),
        (
            ["--import"],
            "Matched 1 out of 1 candidates.",
            "directory",
        ),
    ],
)
def test_settle_info_scenarios(
    temp_ctx: TempContext,
    cli_args: list[str],
    expected_output: str,
    setup_type: str,
) -> None:
    """Test various settle-info command scenarios."""
    with temp_ctx() as ctx:
        ensure_data_exists()
        txn_data = generate_transactions(DEFAULT_TICKERS[0], DEFAULT_TXN_COUNT)
        statement_df = pd.DataFrame(
            [
                {
                    "date": "2025-07-25",
                    "amount": txn_data.iloc[0][Column.Txn.AMOUNT],
                    "currency": txn_data.iloc[0][Column.Txn.CURRENCY].value,
                    "transaction": txn_data.iloc[0][Column.Txn.ACTION].value,
                    "description": (
                        f"{DEFAULT_TICKERS[0]} - "
                        f"{txn_data.iloc[0][Column.Txn.UNITS]} SHARES "
                        f"{txn_data.iloc[0][Column.Txn.TXN_DATE]}"
                    ),
                },
            ],
        )

        if setup_type == "directory":
            new_cli_args = cli_args
            config = ctx.config
            statements_folder = config.statements_path
            statement_file = statements_folder / "test_statement.xlsx"
        else:
            statement_file = ctx.config.project_root / "test_statement.xlsx"
            new_cli_args = [cli_args[0], cli_args[1], str(statement_file)]

        register_test_dataframe(statement_file, statement_df)

        cli_result = _run_cli_with_config(
            ctx.config,
            cli_app,
            ["settle-info", *new_cli_args],
        )
        assert_in_output(expected_output, cli_result)
        assert_cli_success(cli_result)


@pytest.mark.parametrize(
    ("scenario", "cli_args", "query_ids", "expected_output", "setup_action"),
    [
        (
            "default",
            [],
            {
                "brokers": {
                    "ibkr": {
                        "FlexReport": "abc123",
                        "ActivityStatement": "efg456",
                    },
                },
            },
            "ActivityStatement: 3 lines received",
            None,
        ),
        (
            "set_token",
            [],
            None,
            "Flex token stored securely",
            "setup_token_prompt",
        ),
        (
            "reference_code",
            ["-r", "ref123"],
            {"brokers": {"ibkr": {"FlexReport": "abc123"}}},
            "ref123: Received",
            None,
        ),
        (
            "no_queries",
            [],
            None,
            "No transactions downloaded",
            None,
        ),
        (
            "custom_dates",
            ["--from", "2025-10-01", "--to", "2025-10-21"],
            {"brokers": {"ibkr": {"FlexReport": "abc123"}}},
            "FlexReport: 3 lines received",
            None,
        ),
        (
            "db_date",
            [],
            {"brokers": {"ibkr": {"FlexReport": "abc123"}}},
            "Using latest IBKR transaction date: 2025-09-24",
            "setup_db",
        ),
        (
            "credentials_ibkr",
            ["--credentials"],
            None,
            "Setting new IBKR flex token",
            "setup_token_override",
        ),
        (
            "wealthsimple_default",
            ["-b", "wealthsimple"],
            None,
            "Retrieved 6 transactions",
            "setup_wealthsimple",
        ),
        (
            "wealthsimple_custom_dates",
            ["-b", "wealthsimple", "--from", "2025-10-01", "--to", "2025-10-21"],
            None,
            "Retrieved 6 transactions",
            "setup_wealthsimple",
        ),
        (
            "wealthsimple_no_transactions",
            ["-b", "wealthsimple"],
            None,
            "No transactions downloaded",
            "setup_wealthsimple_empty",
        ),
        (
            "wealthsimple_statement",
            ["-b", "wealthsimple", "--statement", "-f", "2024-01-01"],
            None,
            "Retrieved 2 statement transactions",
            "setup_wealthsimple_statement",
        ),
    ],
)
def test_download_scenarios(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    cli_args: list[str],
    query_ids: dict,
    expected_output: str,
    setup_action: str | None,
) -> None:
    """Test various download scenarios."""
    if scenario.startswith("wealthsimple"):
        _test_wealthsimple_scenario(
            temp_ctx,
            monkeypatch,
            cli_args,
            query_ids,
            expected_output,
            setup_action,
        )
    else:
        _test_ibkr_scenario(
            temp_ctx,
            monkeypatch,
            scenario,
            cli_args,
            query_ids,
            expected_output,
            setup_action,
        )


def _setup_ibkr_test_scenario(
    setup_action: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set up IBKR-specific test scenario configurations."""
    if setup_action == "setup_db":
        ensure_data_exists()
        monkeypatch.setattr(
            "cli.commands.download._resolve_from_date",
            lambda from_date_str, broker: _resolve_from_date(
                from_date_str,
                broker,
                account_override="MOCK-ACCOUNT",
            ),
        )
    elif setup_action == "setup_token_prompt":

        def mock_get_token(_self: object) -> str:
            msg = "No token found"
            raise IBKRAuthenticationError(msg)

        def mock_prompt(*_args: object, **_kwargs: object) -> str:
            return "test_token_from_prompt"

        monkeypatch.setattr(
            "services.ibkr_service.IBKRService.get_token",
            mock_get_token,
        )
        monkeypatch.setattr("typer.prompt", mock_prompt)
    elif setup_action == "setup_token_override":

        def mock_prompt(*_args: object, **_kwargs: object) -> str:
            return "test_token_override"

        monkeypatch.setattr("typer.prompt", mock_prompt)


def _test_ibkr_scenario(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    cli_args: list[str],
    query_ids: dict,
    expected_output: str,
    setup_action: str | None,
) -> None:
    """Test IBKR download scenarios."""
    if scenario in ["default", "custom_dates", "reference_code", "db_date"]:
        mock_csv_data = get_default_mock_csv()
    else:
        mock_csv_data = ""

    with (
        temp_ctx(query_ids) as ctx,
        IBKRMockContext(monkeypatch, mock_csv_data) as ibkr_mock,
    ):
        _setup_ibkr_test_scenario(setup_action, monkeypatch)
        cli_result = _run_cli_with_config(ctx.config, cli_app, ["download", *cli_args])

        assert_cli_success(cli_result)
        assert_in_output(expected_output, cli_result)

        if scenario in ["set_token", "no_queries", "token_override"]:
            ibkr_mock.assert_no_csv_written()
        elif scenario in ["default", "custom_dates", "reference_code", "db_date"]:
            ibkr_mock.assert_csv_written(mock_csv_data)


def _test_wealthsimple_scenario(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
    cli_args: list[str],
    query_ids: dict,
    expected_output: str,
    setup_action: str | None,
) -> None:
    """Test Wealthsimple download scenarios."""
    if setup_action == "setup_wealthsimple":
        mock_activities = get_mock_activities()
        expected_csv = get_expected_wealthsimple_csv()
    elif setup_action == "setup_wealthsimple_empty":
        mock_activities = []
        expected_csv = ""
    elif setup_action == "setup_wealthsimple_statement":
        mock_activities = get_mock_statement_transactions()
        expected_csv = get_expected_statement_txn_csv()

    with (
        temp_ctx(query_ids) as ctx,
        WealthsimpleMockContext(
            monkeypatch,
            mock_activities=mock_activities,
        ) as ws_mock,
    ):
        cli_result = _run_cli_with_config(ctx.config, cli_app, ["download", *cli_args])

        assert_cli_success(cli_result)
        assert_in_output(expected_output, cli_result)

        if len(mock_activities) > 0:
            ws_mock.assert_csv_written(expected_csv)
        else:
            ws_mock.assert_no_csv_written()


def test_tickers_command(temp_ctx: TempContext) -> None:
    """Test management of ticker aliases with tickers command."""
    with temp_ctx() as ctx:
        config = ctx.config
        # 1. ADD
        cli_result = _run_cli_with_config(
            config,
            cli_app,
            ["tickers", "--add", "OLD", "NEW", "2025-01-01"],
        )
        assert_cli_success(cli_result)
        assert_in_output("Successfully added alias.", cli_result)
        with get_connection() as conn:
            count = get_row_count(conn, Table.TICKER_ALIASES)
            assert count == 1

        # 2. ADD another alias
        cli_result = _run_cli_with_config(
            config,
            cli_app,
            ["tickers", "--add", "NEW", "NEWEST", "2025-02-01"],
        )
        assert_cli_success(cli_result)

        # 3. LIST aliases
        cli_result = _run_cli_with_config(config, cli_app, ["tickers", "--list"])
        assert_cli_success(cli_result)
        assert_in_output("OLD", cli_result)
        assert_in_output("NEW", cli_result)
        assert_in_output("NEWEST", cli_result)

        # 4. DELETE an alias
        cli_result = _run_cli_with_config(
            config,
            cli_app,
            ["tickers", "--delete", "OLD"],
        )
        assert_cli_success(cli_result)
        assert_in_output("Successfully deleted alias", cli_result)
        with get_connection() as conn:
            count = get_row_count(conn, Table.TICKER_ALIASES)
            assert count == 1
            remaining = get_rows(conn, Table.TICKER_ALIASES)
            assert remaining.iloc[0][Column.Aliases.OLD_TICKER] == "NEW"


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
) -> CliTestResult:
    """Run CLI commands with proper config mocking and capture plain output."""
    if args is None:
        args = []

    # Mock bootstrap.reload_config to return our test config
    with patch("app.bootstrap.reload_config") as mock_reload, capture_output() as bio:
        mock_reload.return_value = config
        click_result = runner.invoke(command_app, args)
        return CliTestResult(
            exit_code=click_result.exit_code,
            stdout=click_result.stdout,
            stderr=click_result.stderr,
            exception=click_result.exception,
            plain_output=bio.get_text(),
        )


def assert_cli_success(result: Result) -> None:  # pragma: no cover
def assert_cli_success(result: CliTestResult) -> None:  # pragma: no cover
    if result.exit_code != 0:
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        print("PLAIN_OUTPUT:\n", result.plain_output)
        if result.exception:
            print("EXCEPTION:", str(result.exception))
    assert result.exit_code == 0, (
        f"CLI failed: {result.exit_code}\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}\n"
        f"PLAIN_OUTPUT:\n{result.plain_output}\n"
        f"EXCEPTION:\n{str(result.exception) if result.exception else 'None'}"
    )


def assert_in_output(expected_substring: str, cli_result: CliTestResult) -> None:
    """Assert that a substring exists in the CLI's plain text output."""
    if expected_substring not in cli_result.plain_output:
        print("\n---EXPECTED SUBSTRING---")
        print(expected_substring)
        print("\n---ACTUAL PLAIN_OUTPUT---")
        print(cli_result.plain_output)
        print("---END PLAIN_OUTPUT---\n")
        pytest.fail(
            "Expected substring was not found in the command's plain text output.",
        )


def assert_not_in_output(unexpected_substring: str, cli_result: CliTestResult) -> None:
    """Assert that a substring does NOT exist in the CLI's plain text output."""
    if unexpected_substring in cli_result.plain_output:
        print("\n---UNEXPECTED SUBSTRING---")
        print(unexpected_substring)
        print("\n---ACTUAL PLAIN_OUTPUT (highlighted)---")
        highlighted_output = cli_result.plain_output.replace(
            unexpected_substring,
            f">>>{unexpected_substring}<<<",
        )
        print(highlighted_output)
        print("---END PLAIN_OUTPUT---\n")
        pytest.fail(
            "Unexpected substring was found in the command's plain text output.",
        )


