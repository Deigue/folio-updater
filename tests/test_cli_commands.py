"""Tests for CLI commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable
from unittest.mock import patch

import pandas as pd
import pytest
from openpyxl import load_workbook
from typer.testing import CliRunner

from cli.commands import import_cmd
from cli.main import app as cli_app
from db import db
from mock.folio_setup import ensure_folio_exists
from utils.constants import Column, Table

if TYPE_CHECKING:
    from contextlib import _GeneratorContextManager
    from pathlib import Path

    from click.testing import Result
    from typer import Typer

    from app.app_context import AppContext
    from utils.config import Config

# Use basic runner configuration
runner = CliRunner()

# Constants for test assertions
EXPECTED_TRANSACTION_COUNT = 2
TYPER_INVALID_COMMAND_EXIT_CODE = 2


@pytest.fixture(autouse=True)
def suppress_logging_conflicts() -> db.Generator[None, Any, None]:
    """Suppress logging to avoid stream conflicts during CLI testing."""
    # Temporarily disable all loggers to avoid stream conflicts
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


def run_cli_with_config(
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


class TestDemoCommand:
    """Test the demo command functionality."""

    def run_demo_command(
        self,
        temp_config: Callable[
            ...,
            _GeneratorContextManager[AppContext, None, None],
        ],
    ) -> None:
        """Test demo command through main CLI app."""
        with temp_config() as ctx:
            config = ctx.config
            assert not config.folio_path.exists()
            assert not config.db_path.exists()
            result = run_cli_with_config(config, cli_app, ["demo"])
            assert result.exit_code == 0
            assert "Demo portfolio created successfully!" in result.stdout
            assert config.folio_path.exists()
            assert config.db_path.exists()
            with db.get_connection() as conn:
                count = db.get_row_count(conn, Table.TXNS.value)
                assert count > 0


class TestFXCommand:
    """Test the getfx command functionality."""

    @pytest.mark.no_mock_forex
    def test_getfx_command(
        self,
        temp_config: Callable[
            ...,
            _GeneratorContextManager[AppContext, None, None],
        ],
    ) -> None:
        """Test getfx command through main CLI app."""
        with temp_config() as ctx:
            config = ctx.config
            ensure_folio_exists()

            # Remove forex data from folio to simulate fresh state.
            if config.folio_path.exists():
                workbook = load_workbook(config.folio_path)
                if config.forex_sheet() in workbook.sheetnames:
                    workbook.remove(workbook[config.forex_sheet()])
                    workbook.save(config.folio_path)
                workbook.close()

            # Drop the FX rates table to force fresh fetch
            with db.get_connection() as conn:
                conn.execute(f"DROP TABLE IF EXISTS {Table.FX.value}")
                conn.commit()

            result = run_cli_with_config(config, cli_app, ["getfx"])
            assert result.exit_code == 0
            assert "Successfully updated" in result.stdout
            with db.get_connection() as conn:
                count = db.get_row_count(conn, Table.FX.value)
                assert count > 0


class TestImportCommand:
    """Test the import command functionality."""

    def _create_test_excel_file(
        self,
        file_path: Path,
        sheet_name: str,
    ) -> None:
        """Create a test Excel file with sample transaction data."""
        test_data = {
            Column.Txn.TXN_DATE.value: ["2024-01-01", "2024-01-02"],
            Column.Txn.ACTION.value: ["BUY", "SELL"],
            Column.Txn.AMOUNT.value: [1000.0, 2000.0],
            Column.Txn.CURRENCY.value: ["USD", "USD"],
            Column.Txn.PRICE.value: [100.0, 200.0],
            Column.Txn.UNITS.value: [10.0, 10.0],
            Column.Txn.TICKER.value: ["AAPL", "MSFT"],
            Column.Txn.ACCOUNT.value: ["TEST-ACCOUNT", "TEST-ACCOUNT"],
        }

        df = pd.DataFrame(test_data)
        df.to_excel(file_path, index=False, sheet_name=sheet_name)

    def test_import_command_default(
        self,
        temp_config: Callable[
            ...,
            _GeneratorContextManager[AppContext, None, None],
        ],
    ) -> None:
        """Test import command default behavior (import from configured folio file)."""
        with temp_config() as ctx:
            config = ctx.config

            # Create a folio file with test data
            self._create_test_excel_file(
                config.folio_path,
                config.transactions_sheet(),
            )

            # Run import command without arguments
            result = run_cli_with_config(config, import_cmd.app)
            assert result.exit_code == 0
            assert (
                f"Successfully imported {EXPECTED_TRANSACTION_COUNT} transactions"
                in result.stdout
            )

            # Verify data was imported to database
            with db.get_connection() as conn:
                count = db.get_row_count(conn, Table.TXNS.value)
                assert count == EXPECTED_TRANSACTION_COUNT

    def test_import_command_missing_folio(
        self,
        temp_config: Callable[
            ...,
            _GeneratorContextManager[AppContext, None, None],
        ],
    ) -> None:
        """Test import command when folio file doesn't exist."""
        with temp_config() as ctx:
            config = ctx.config
            assert not config.folio_path.exists()
            result = run_cli_with_config(config, import_cmd.app)
            assert result.exit_code == 1
            assert f"Folio file not found: {config.folio_path}" in result.stderr

