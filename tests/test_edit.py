"""Tests for the edit command."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pandas as pd
import pytest

from cli.commands import edit as edit_module
from cli.main import app as cli_app
from datagen import ensure_data_exists
from db import get_connection, get_rows
from utils.constants import Action, Column, Currency, Table

from .helpers.cli import (
    assert_cli_success,
    assert_in_output,
    assert_not_in_output,
    run_cli_with_config,
)
from .helpers.seed import ACCOUNT, TICKER, seed_transaction

if TYPE_CHECKING:
    from collections.abc import Generator

    from tests.test_types import TempContext


@pytest.fixture(autouse=True)
def suppress_logging_conflicts() -> Generator[None, Any]:
    """Suppress logging to avoid stream conflicts during CLI testing."""
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


def _row(txn_id: int) -> pd.Series:
    """Fetch a single transaction by TxnId."""
    with get_connection() as conn:
        rows = get_rows(
            conn,
            Table.TXNS,
            where=f'"{Column.Txn.TXN_ID}" = ?',
            params=[txn_id],
        )
    assert len(rows) == 1, f"Expected TxnId {txn_id} to exist"
    return rows.iloc[0]


class TestEditLiteral:
    """Literal `--set Field=value` edits."""

    def test_edit_single_field(self, temp_ctx: TempContext) -> None:
        """A literal value replaces the stored one."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "Price=175.20", "--force"],
            )

            assert_cli_success(cli_result)
            assert Decimal(str(_row(txn_id)[Column.Txn.PRICE])) == Decimal("175.20")

    def test_edit_multiple_fields(self, temp_ctx: TempContext) -> None:
        """Several --set options apply in one call."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                [
                    "edit",
                    str(txn_id),
                    "--set",
                    "Price=175.20",
                    "--set",
                    "Units=20",
                    "--force",
                ],
            )

            assert_cli_success(cli_result)
            row = _row(txn_id)
            assert Decimal(str(row[Column.Txn.PRICE])) == Decimal("175.20")
            assert Decimal(str(row[Column.Txn.UNITS])) == Decimal(20)

    def test_untouched_columns_are_preserved(self, temp_ctx: TempContext) -> None:
        """Only the edited column moves."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()
            original = _row(txn_id)

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "Price=175.20", "--force"],
            )

            assert_cli_success(cli_result)
            updated = _row(txn_id)
            for column in (
                Column.Txn.TXN_DATE,
                Column.Txn.ACTION,
                Column.Txn.AMOUNT,
                Column.Txn.UNITS,
                Column.Txn.TICKER,
                Column.Txn.ACCOUNT,
            ):
                assert updated[column] == original[column]

    def test_other_rows_are_untouched(self, temp_ctx: TempContext) -> None:
        """Rows outside the selection are not rewritten."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            edited_id = seed_transaction(amount="-1502.50")
            other_id = seed_transaction(ticker="OTHERTKR", amount="-900.00")
            other_before = _row(other_id)

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(edited_id), "--set", "Price=175.20", "--force"],
            )

            assert_cli_success(cli_result)
            assert _row(other_id).equals(other_before)


class TestEditArithmetic:
    """Arithmetic `--set Field*=N` edits against current values."""

    @pytest.mark.parametrize(
        ("operation", "expected"),
        [
            ("Price*=10", "1502.5"),
            ("Price/=10", "15.025"),
            ("Price+=5", "155.25"),
            ("Price-=0.25", "150"),
        ],
    )
    def test_operators(
        self,
        temp_ctx: TempContext,
        operation: str,
        expected: str,
    ) -> None:
        """Each operator computes against the row's current value."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", operation, "--force"],
            )

            assert_cli_success(cli_result)
            assert Decimal(str(_row(txn_id)[Column.Txn.PRICE])) == Decimal(expected)

    def test_arithmetic_is_exact(self, temp_ctx: TempContext) -> None:
        """Decimal maths avoids binary float drift."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction(price="17.52")

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "Price*=10", "--force"],
            )

            assert_cli_success(cli_result)
            assert Decimal(str(_row(txn_id)[Column.Txn.PRICE])) == Decimal("175.2")

    def test_batch_arithmetic_by_query_terms(self, temp_ctx: TempContext) -> None:
        """Query terms apply the same operation to every matched row."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            first = seed_transaction(price="10", amount="-100.00")
            second = seed_transaction(price="20", amount="-200.00")

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", TICKER, "--set", "Price*=10", "--force"],
            )

            assert_cli_success(cli_result)
            assert Decimal(str(_row(first)[Column.Txn.PRICE])) == Decimal(100)
            assert Decimal(str(_row(second)[Column.Txn.PRICE])) == Decimal(200)

    def test_split_migration_scenario(self, temp_ctx: TempContext) -> None:
        """The NVDA split un-adjustment from the issue, end to end."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            # History that was manually split-adjusted: price /10, units *10.
            txn_id = seed_transaction(
                ticker="NVDA",
                date="2025-08-01",
                price="12.34",
                units="100",
                amount="-1234.00",
            )
            split = run_cli_with_config(
                ctx.config,
                cli_app,
                [
                    "add",
                    "--action",
                    "SPLIT",
                    "--date",
                    "2025-08-10",
                    "--account",
                    ACCOUNT,
                    "--currency",
                    Currency.USD.value,
                    "--ticker",
                    "NVDA",
                    "--price",
                    "1",
                    "--units",
                    "10",
                ],
            )
            assert_cli_success(split)

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                [
                    "edit",
                    "NVDA",
                    "before",
                    "2025-08-10",
                    "--set",
                    "Price*=10",
                    "--set",
                    "Units/=10",
                    "--force",
                ],
            )

            assert_cli_success(cli_result)
            row = _row(txn_id)
            assert Decimal(str(row[Column.Txn.PRICE])) == Decimal("123.4")
            assert Decimal(str(row[Column.Txn.UNITS])) == Decimal(10)


class TestEditSettlement:
    """Settlement date reconciliation."""

    def test_manual_settle_date_clears_the_calculated_flag(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """An explicit SettleDate is authoritative and loses the tag."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()
            assert _row(txn_id)[Column.Txn.SETTLE_CALCULATED] == 1

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "SettleDate=2025-08-29", "--force"],
            )

            assert_cli_success(cli_result)
            row = _row(txn_id)
            assert row[Column.Txn.SETTLE_DATE] == "2025-08-29"
            assert row[Column.Txn.SETTLE_CALCULATED] == 0

    def test_txn_date_edit_recalculates_a_calculated_date(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """A calculated settlement date follows its transaction date."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()
            before = _row(txn_id)
            assert before[Column.Txn.SETTLE_CALCULATED] == 1

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "TxnDate=2025-08-20", "--force"],
            )

            assert_cli_success(cli_result)
            row = _row(txn_id)
            assert row[Column.Txn.TXN_DATE] == "2025-08-20"
            assert row[Column.Txn.SETTLE_DATE] > "2025-08-20"
            assert row[Column.Txn.SETTLE_CALCULATED] == 1

    def test_txn_date_edit_keeps_a_manual_date(self, temp_ctx: TempContext) -> None:
        """A manually set settlement date is never recalculated."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()
            manual = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "SettleDate=2025-08-29", "--force"],
            )
            assert_cli_success(manual)

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "TxnDate=2025-08-20", "--force"],
            )

            assert_cli_success(cli_result)
            row = _row(txn_id)
            assert row[Column.Txn.SETTLE_DATE] == "2025-08-29"
            assert row[Column.Txn.SETTLE_CALCULATED] == 0

    def test_invalid_settle_date_is_rejected(self, temp_ctx: TempContext) -> None:
        """A bad SettleDate errors instead of being silently recalculated."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()
            before = _row(txn_id)

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "SettleDate=notadate", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("is not a valid date", cli_result)
            assert _row(txn_id).equals(before)


class TestEditValidation:
    """Failures that abort the edit before anything is written."""

    def test_arithmetic_on_non_numeric_field(self, temp_ctx: TempContext) -> None:
        """Arithmetic is refused on a field that does not hold numbers."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "Ticker*=2", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("not a numeric field", cli_result)

    def test_arithmetic_on_empty_value_aborts_the_batch(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """One row with no value to compute from aborts the whole edit."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            priced = seed_transaction(price="10", amount="-100.00")
            unpriced = seed_transaction(
                action="DIVIDEND",
                amount="25.00",
                price=None,
                units=None,
            )
            before = _row(priced)

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", TICKER, "--set", "Price*=10", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("current value is empty", cli_result)
            assert _row(priced).equals(before)
            assert pd.isna(_row(unpriced)[Column.Txn.PRICE])

    def test_malformed_set_value(self, temp_ctx: TempContext) -> None:
        """A --set without an '=' is rejected."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "justakey", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("Expected Field=VALUE", cli_result)

    def test_unknown_field(self, temp_ctx: TempContext) -> None:
        """Editing never invents a column - a typo is an error."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "Prcie=1", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("Unknown field 'Prcie'", cli_result)

    def test_field_absent_from_the_folio(self, temp_ctx: TempContext) -> None:
        """A known field the folio does not have is an error, not a new column."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "Fee=1.50", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("is not a column in this folio", cli_result)

    def test_repeated_field(self, temp_ctx: TempContext) -> None:
        """The same field twice is an error, not a silent last-wins."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                [
                    "edit",
                    str(txn_id),
                    "--set",
                    "Price=1",
                    "--set",
                    "Price=2",
                    "--force",
                ],
            )

            assert cli_result.exit_code == 1
            assert_in_output("was given more than once", cli_result)

    def test_protected_columns_are_refused(self, temp_ctx: TempContext) -> None:
        """TxnId cannot be reassigned."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "TxnId=5", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("cannot be edited directly", cli_result)

    def test_division_by_zero(self, temp_ctx: TempContext) -> None:
        """Dividing by zero is caught up front."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "Price/=0", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("Cannot divide", cli_result)

    def test_non_numeric_operand(self, temp_ctx: TempContext) -> None:
        """An operand that is not a number is rejected."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "Price*=abc", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("is not a number", cli_result)

    def test_invalid_txn_date(self, temp_ctx: TempContext) -> None:
        """A bad transaction date is rejected."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "TxnDate=notadate", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("is not a valid date", cli_result)

    def test_clearing_a_required_field_is_rejected(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """An edit cannot write a row that `folio add` would reject."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()
            before = _row(txn_id)

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "Amount=", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("would produce invalid transaction(s)", cli_result)
            assert _row(txn_id).equals(before)


class TestEditDuplicates:
    """Duplicate detection that excludes the rows being edited."""

    def test_no_op_edit_reports_no_changes(self, temp_ctx: TempContext) -> None:
        """Setting a field to the value it already has is not a duplicate."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "Price=150.25", "--force"],
            )

            assert_cli_success(cli_result)
            assert_in_output("No changes to apply.", cli_result)

    def test_non_essential_edit_does_not_self_duplicate(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """Editing only a non-essential field never clashes with itself."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction(fee="4.95")

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "Fee=1.50"],
                user_input="y\n",
            )

            assert_cli_success(cli_result)
            assert_not_in_output("create duplicate transaction(s)", cli_result)
            assert Decimal(str(_row(txn_id)[Column.Txn.FEE])) == Decimal("1.50")

    def test_edit_the_only_transaction(self, temp_ctx: TempContext) -> None:
        """With nothing else in the folio there is nothing to clash with."""
        with temp_ctx() as ctx:
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "Price=175.20", "--force"],
            )

            assert_cli_success(cli_result)
            assert Decimal(str(_row(txn_id)[Column.Txn.PRICE])) == Decimal("175.20")

    def test_edit_onto_another_row_prompts(self, temp_ctx: TempContext) -> None:
        """An edit that would duplicate an existing row asks first."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            target = seed_transaction(price="150.25", amount="-1502.50")
            other = seed_transaction(price="999.99", amount="-1502.50")
            before = _row(other)

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(other), "--set", "Price=150.25"],
                user_input="y\nn\n",
            )

            assert_cli_success(cli_result)
            assert_in_output("existing transaction(s) in the folio", cli_result)
            assert_in_output("No transactions edited.", cli_result)
            assert _row(other).equals(before)
            assert Decimal(str(_row(target)[Column.Txn.PRICE])) == Decimal("150.25")

    def test_force_applies_a_duplicating_edit(self, temp_ctx: TempContext) -> None:
        """--force accepts the duplicate without prompting."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            seed_transaction(price="150.25", amount="-1502.50")
            other = seed_transaction(price="999.99", amount="-1502.50")

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(other), "--set", "Price=150.25", "--force"],
            )

            assert_cli_success(cli_result)
            assert Decimal(str(_row(other)[Column.Txn.PRICE])) == Decimal("150.25")


class TestEditSigns:
    """Sign normalization applies to edits, as it does to imports."""

    def test_buy_amount_is_forced_negative(self, temp_ctx: TempContext) -> None:
        """A positive Amount on a BUY is corrected on the way in."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "Amount=3000", "--force"],
            )

            assert_cli_success(cli_result)
            row = _row(txn_id)
            assert row[Column.Txn.ACTION] == Action.BUY
            assert Decimal(str(row[Column.Txn.AMOUNT])) == Decimal(-3000)


class TestEditFlow:
    """Preview, confirmation, and side effects."""

    def test_preview_shows_before_and_after(self, temp_ctx: TempContext) -> None:
        """The pending change is rendered with both values."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "Price=175.20", "--dry-run"],
            )

            assert_cli_success(cli_result)
            assert_in_output("Pending Changes", cli_result)
            assert_in_output("150.25", cli_result)
            assert_in_output("175.2", cli_result)

    def test_dry_run_writes_nothing(self, temp_ctx: TempContext) -> None:
        """--dry-run previews without writing."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()
            before = _row(txn_id)

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "Price=175.20", "--dry-run"],
            )

            assert_cli_success(cli_result)
            assert_in_output("Dry run - nothing was written to the folio.", cli_result)
            assert _row(txn_id).equals(before)

    def test_declining_writes_nothing(self, temp_ctx: TempContext) -> None:
        """Answering no leaves the transaction alone."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()
            before = _row(txn_id)

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "Price=175.20"],
                user_input="n\n",
            )

            assert_cli_success(cli_result)
            assert_in_output("No transactions edited.", cli_result)
            assert _row(txn_id).equals(before)

    def test_confirming_applies(self, temp_ctx: TempContext) -> None:
        """Answering yes writes the edit."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "Price=175.20"],
                user_input="y\n",
            )

            assert_cli_success(cli_result)
            assert Decimal(str(_row(txn_id)[Column.Txn.PRICE])) == Decimal("175.20")

    @pytest.mark.real_parquet_export
    def test_edit_exports_to_parquet(self, temp_ctx: TempContext) -> None:
        """A successful edit refreshes the transactions parquet file."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", str(txn_id), "--set", "Price=175.20", "--force"],
            )

            assert_cli_success(cli_result)
            exported = pd.read_parquet(ctx.config.txn_parquet)
            prices = exported.loc[
                exported[Column.Txn.TICKER] == TICKER,
                Column.Txn.PRICE,
            ]
            assert Decimal(str(prices.iloc[0])) == Decimal("175.20")

    def test_edit_is_logged(self, temp_ctx: TempContext) -> None:
        """Edits are recorded in the shared importer audit log."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            with patch.object(edit_module, "import_logger") as mock_logger:
                cli_result = run_cli_with_config(
                    ctx.config,
                    cli_app,
                    ["edit", str(txn_id), "--set", "Price=175.20", "--force"],
                )

            assert_cli_success(cli_result)
            logged = " ".join(str(call) for call in mock_logger.info.call_args_list)
            assert "EDIT TXN (manual, %s) SET %s" in logged
            assert "Price=175.20" in logged
            assert " ~ TxnId %s: %s" in logged
            assert "DONE: %d transaction(s) updated (TxnIds %s)" in logged


class TestEditSelection:
    """Selection guards, shared with `folio delete`."""

    def test_unknown_txn_id_fails(self, temp_ctx: TempContext) -> None:
        """An unknown TxnId is an error."""
        with temp_ctx() as ctx:
            ensure_data_exists()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", "999999", "--set", "Price=1", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("No transaction with TxnId 999999.", cli_result)

    def test_no_matches_fails(self, temp_ctx: TempContext) -> None:
        """Query terms matching nothing are an error."""
        with temp_ctx() as ctx:
            ensure_data_exists()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", "ticker:NOSUCHTICKER", "--set", "Price=1", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("No transactions matched", cli_result)

    def test_unbounded_selection_is_refused(self, temp_ctx: TempContext) -> None:
        """A selection with no filters cannot rewrite the whole folio."""
        with temp_ctx() as ctx:
            ensure_data_exists()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["edit", "sort:Amount", "--set", "Price=1", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("Refusing to edit every transaction", cli_result)
