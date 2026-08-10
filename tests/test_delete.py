"""Tests for the delete command."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pandas as pd
import pytest

from cli.commands import delete as delete_module
from cli.main import app as cli_app
from datagen import ensure_data_exists
from db import get_connection, get_row_count, get_rows
from utils.constants import Column, Table

from .helpers.cli import (
    assert_cli_success,
    assert_in_output,
    assert_not_in_output,
    run_cli_with_config,
)
from .helpers.seed import TICKER, seed_transaction

if TYPE_CHECKING:
    from collections.abc import Generator

    from tests.test_types import TempContext


@pytest.fixture(autouse=True)
def suppress_logging_conflicts() -> Generator[None, Any]:
    """Suppress logging to avoid stream conflicts during CLI testing."""
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


def _txn_count() -> int:
    """Get the current number of transactions in the database."""
    with get_connection() as conn:
        return get_row_count(conn, Table.TXNS)


def _txn_ids() -> list[int]:
    """Get every TxnId currently in the database."""
    with get_connection() as conn:
        rows = get_rows(conn, Table.TXNS)
    return [int(txn_id) for txn_id in rows[Column.Txn.TXN_ID]]


class TestDeleteSelection:
    """Selecting the transactions to delete."""

    def test_delete_single_txn_id(self, temp_ctx: TempContext) -> None:
        """A single TxnId deletes exactly that transaction."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()
            before = _txn_count()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["delete", str(txn_id), "--force"],
            )

            assert_cli_success(cli_result)
            assert _txn_count() == before - 1
            assert txn_id not in _txn_ids()

    def test_delete_multiple_txn_ids(self, temp_ctx: TempContext) -> None:
        """Several TxnIds are all deleted in one call."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            first = seed_transaction(amount="-1502.50")
            second = seed_transaction(amount="-1602.50")
            before = _txn_count()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["delete", str(first), str(second), "--force"],
            )

            assert_cli_success(cli_result)
            assert _txn_count() == before - 2
            remaining = _txn_ids()
            assert first not in remaining
            assert second not in remaining

    def test_repeated_txn_id_deletes_once(self, temp_ctx: TempContext) -> None:
        """A TxnId given twice is deduplicated rather than counted twice."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()
            before = _txn_count()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["delete", str(txn_id), str(txn_id), "--force"],
            )

            assert_cli_success(cli_result)
            assert_in_output("Deleted 1 transaction(s)", cli_result)
            assert _txn_count() == before - 1

    def test_delete_by_query_terms(self, temp_ctx: TempContext) -> None:
        """Query terms select a batch the same way `folio query` would."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            seed_transaction(amount="-1502.50")
            seed_transaction(amount="-1602.50")
            before = _txn_count()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["delete", TICKER, "--force"],
            )

            assert_cli_success(cli_result)
            assert _txn_count() == before - 2
            with get_connection() as conn:
                remaining = get_rows(
                    conn,
                    Table.TXNS,
                    where=f'"{Column.Txn.TICKER}" = ?',
                    params=[TICKER],
                )
            assert remaining.empty


class TestDeleteConfirmation:
    """Preview, confirmation and the flags that shortcut them."""

    def test_preview_shows_matched_rows(self, temp_ctx: TempContext) -> None:
        """The matched transactions are shown before the prompt."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["delete", str(txn_id)],
                user_input="n\n",
            )

            assert_cli_success(cli_result)
            assert_in_output("Transactions to Delete", cli_result)
            assert_in_output(TICKER, cli_result)

    def test_declining_deletes_nothing(self, temp_ctx: TempContext) -> None:
        """Answering no leaves the folio untouched."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()
            before = _txn_count()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["delete", str(txn_id)],
                user_input="n\n",
            )

            assert_cli_success(cli_result)
            assert_in_output("No transactions deleted.", cli_result)
            assert _txn_count() == before
            assert txn_id in _txn_ids()

    def test_confirming_deletes(self, temp_ctx: TempContext) -> None:
        """Answering yes deletes exactly the previewed transaction."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()
            before = _txn_count()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["delete", str(txn_id)],
                user_input="y\n",
            )

            assert_cli_success(cli_result)
            assert _txn_count() == before - 1
            assert txn_id not in _txn_ids()

    def test_force_skips_the_prompt(self, temp_ctx: TempContext) -> None:
        """--force deletes without asking."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["delete", str(txn_id), "--force"],
            )

            assert_cli_success(cli_result)
            assert "Delete 1 transaction(s)?" not in cli_result.stdout
            assert txn_id not in _txn_ids()

    def test_dry_run_writes_nothing(self, temp_ctx: TempContext) -> None:
        """--dry-run previews without deleting or prompting."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()
            before = _txn_count()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["delete", str(txn_id), "--dry-run"],
            )

            assert_cli_success(cli_result)
            assert_in_output("Dry run - nothing was written to the folio.", cli_result)
            assert_not_in_output("Deleted", cli_result)
            assert _txn_count() == before
            assert txn_id in _txn_ids()

    def test_bulk_delete_warns_about_the_share(self, temp_ctx: TempContext) -> None:
        """A selection covering most of the folio warns before the prompt."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            total = _txn_count()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["delete", f"limit:{total}"],
                user_input="n\n",
            )

            assert_cli_success(cli_result)
            assert_in_output(f"This will delete {total} of {total}", cli_result)
            assert _txn_count() == total


class TestDeleteGuards:
    """Refusals that happen before anything is written."""

    def test_unknown_txn_id_fails(self, temp_ctx: TempContext) -> None:
        """An unknown TxnId is an error, not a silent no-op."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            before = _txn_count()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["delete", "999999", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("No transaction with TxnId 999999.", cli_result)
            assert _txn_count() == before

    def test_partially_valid_id_list_deletes_nothing(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """One bad id in the list aborts the whole delete."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()
            before = _txn_count()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["delete", str(txn_id), "999999", "--force"],
            )

            assert cli_result.exit_code == 1
            assert _txn_count() == before
            assert txn_id in _txn_ids()

    def test_no_matches_fails(self, temp_ctx: TempContext) -> None:
        """Query terms matching nothing are an error."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            before = _txn_count()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["delete", "ticker:NOSUCHTICKER", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("No transactions matched", cli_result)
            assert _txn_count() == before

    def test_unbounded_selection_is_refused(self, temp_ctx: TempContext) -> None:
        """A selection with no filters cannot wipe the folio."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            before = _txn_count()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["delete", "sort:Amount"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("Refusing to delete every transaction", cli_result)
            assert _txn_count() == before

    def test_force_does_not_bypass_the_unbounded_guard(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """--force skips the prompt, never the safety rail."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            before = _txn_count()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["delete", "sort:Amount", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("Refusing to delete every transaction", cli_result)
            assert _txn_count() == before

    def test_limit_only_selection_is_allowed(self, temp_ctx: TempContext) -> None:
        """`limit:N` bounds a selection even without filters."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            before = _txn_count()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["delete", "limit:2", "--force"],
            )

            assert_cli_success(cli_result)
            assert _txn_count() == before - 2


class TestDeleteSideEffects:
    """Backup, parquet re-export and the audit log."""

    @pytest.mark.real_parquet_export
    def test_delete_exports_to_parquet(self, temp_ctx: TempContext) -> None:
        """A successful delete refreshes the transactions parquet file."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["delete", str(txn_id), "--force"],
            )

            assert_cli_success(cli_result)
            exported = pd.read_parquet(ctx.config.txn_parquet)
            assert TICKER not in set(exported[Column.Txn.TICKER])

    def test_delete_is_logged(self, temp_ctx: TempContext) -> None:
        """Deletions are recorded in the shared importer audit log."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            with patch.object(delete_module, "import_logger") as mock_logger:
                cli_result = run_cli_with_config(
                    ctx.config,
                    cli_app,
                    ["delete", str(txn_id), "--force"],
                )

            assert_cli_success(cli_result)
            logged = " ".join(str(call) for call in mock_logger.info.call_args_list)
            assert "DELETE TXN (manual, %s)" in logged
            assert f"TxnIds {txn_id}" in logged
            assert "DONE: %d transaction(s) deleted (TxnIds %s)" in logged
