"""Tests for the edit command."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import patch

import pandas as pd
import pytest

from cli.main import app as cli_app
from datagen import ensure_data_exists
from db import get_connection, get_rows, txns
from domain import Action, Column, Currency, Table
from ingest.changes import (
    Arithmetic,
    Assign,
    EditPlan,
    PlanRefusedError,
    SetOperation,
    plan_edit,
)

from .helpers.cli import (
    assert_cli_success,
    assert_in_output,
    run_cli,
)
from .helpers.seed import ACCOUNT, TICKER, seed_transaction

if TYPE_CHECKING:
    from .helpers.context import TempContext


pytestmark = pytest.mark.usefixtures("quiet_logging")


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


def _plan(txn_ids: list[int], *operations: SetOperation) -> EditPlan:
    """Plan an edit of the given stored transactions."""
    return plan_edit(txns.by_ids(txn_ids), operations)


def _assign(column: str, literal: str) -> Assign:
    return Assign(column=column, raw=f"{column}={literal}", literal=literal)


def _times(column: str, operand: str) -> Arithmetic:
    return Arithmetic(
        column=column,
        raw=f"{column}*={operand}",
        operator="*",
        operand=Decimal(operand),
    )


class TestEditPlanSettlement:
    """Settlement dates follow the edit, on the plan."""

    def test_a_manual_settle_date_is_no_longer_calculated(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """An explicit SettleDate is authoritative and loses the tag."""
        with temp_ctx():
            txn_id = seed_transaction()

            [after] = _plan(
                [txn_id],
                _assign(Column.Txn.SETTLE_DATE, "2025-08-29"),
            ).after

            assert after.settle_date == "2025-08-29"
            assert not after.settle_calculated

    def test_a_calculated_date_follows_its_transaction_date(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """Moving the transaction date recalculates a calculated settlement date."""
        with temp_ctx():
            txn_id = seed_transaction()

            [after] = _plan([txn_id], _assign(Column.Txn.TXN_DATE, "2025-08-20")).after

            assert after.txn_date == "2025-08-20"
            assert after.settle_date is not None
            assert after.settle_date > "2025-08-20"
            assert after.settle_calculated

    def test_a_manual_date_stays_put(self, temp_ctx: TempContext) -> None:
        """A manually set settlement date is never recalculated."""
        with temp_ctx():
            txn_id = seed_transaction(settle_date="2025-08-29")

            [after] = _plan([txn_id], _assign(Column.Txn.TXN_DATE, "2025-08-20")).after

            assert after.settle_date == "2025-08-29"
            assert not after.settle_calculated


class TestEditPlanRefusals:
    """Edits the plan refuses, with the reason."""

    def test_arithmetic_on_an_empty_value_refuses_the_batch(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """One transaction with no value to compute from refuses the whole edit."""
        with temp_ctx():
            priced = seed_transaction(price="10", amount="-100.00")
            unpriced = seed_transaction(
                action="DIVIDEND",
                amount="25.00",
                price=None,
                units=None,
            )

            with pytest.raises(PlanRefusedError) as refused:
                _plan([priced, unpriced], _times(Column.Txn.PRICE, "10"))

            assert refused.value.reason == (
                f"Cannot apply 'Price*=10' on TxnId {unpriced}: current value is empty."
            )

    def test_an_invalid_result_names_the_transaction(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """Clearing a required field refuses the edit, naming the transaction."""
        with temp_ctx():
            txn_id = seed_transaction()

            with pytest.raises(PlanRefusedError) as refused:
                _plan([txn_id], _assign(Column.Txn.AMOUNT, ""))

            assert (
                refused.value.reason == "The edit would produce invalid transaction(s):"
            )
            [detail] = refused.value.details
            assert detail.startswith(f"TxnId {txn_id}: ")


class TestEditPlanDuplicates:
    """Duplicates an edit would create, leaving out the rows being edited."""

    def test_a_same_value_edit_changes_nothing(self, temp_ctx: TempContext) -> None:
        """Setting a whole number to the value it holds is no change and no clash."""
        with temp_ctx():
            txn_id = seed_transaction(amount="-100")

            plan = _plan([txn_id], _assign(Column.Txn.AMOUNT, "-100"))

            assert plan.changed == []
            assert not plan.duplicating

    def test_a_non_essential_edit_does_not_match_itself(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """The transaction's own pre-edit self is not a duplicate."""
        with temp_ctx():
            txn_id = seed_transaction(fee="4.95")

            plan = _plan([txn_id], _assign(Column.Txn.FEE, "1.50"))

            assert plan.changed == [Column.Txn.FEE]
            assert not plan.duplicating

    def test_an_edit_onto_another_row_names_it(self, temp_ctx: TempContext) -> None:
        """Matching a stored transaction outside the edit names that transaction."""
        with temp_ctx():
            target = seed_transaction(price="150.25", amount="-1502.50")
            other = seed_transaction(price="999.99", amount="-1502.50")

            plan = _plan([other], _assign(Column.Txn.PRICE, "150.25"))

            assert [txn.txn_id for txn in plan.duplicates] == [target]
            assert not plan.makes_identical

    def test_two_edited_transactions_made_identical(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """An edit that makes two selected transactions the same is a duplicate."""
        with temp_ctx():
            first = seed_transaction(price="10", amount="-100.00")
            second = seed_transaction(price="20", amount="-100.00")

            plan = _plan([first, second], _assign(Column.Txn.PRICE, "30"))

            assert plan.makes_identical
            assert plan.duplicates == []


class TestEditLiteral:
    """Literal `--set Field=value` edits."""

    def test_edit_single_field(self, temp_ctx: TempContext) -> None:
        """A literal value replaces the stored one."""
        with temp_ctx():
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli(
                cli_app,
                ["edit", str(txn_id), "--set", "Price=175.20", "--force"],
            )

            assert_cli_success(cli_result)
            assert Decimal(str(_row(txn_id)[Column.Txn.PRICE])) == Decimal("175.20")

    def test_edit_multiple_fields(self, temp_ctx: TempContext) -> None:
        """Several --set options apply in one call."""
        with temp_ctx():
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli(
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
        with temp_ctx():
            ensure_data_exists()
            txn_id = seed_transaction()
            original = _row(txn_id)

            cli_result = run_cli(
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
        with temp_ctx():
            ensure_data_exists()
            edited_id = seed_transaction(amount="-1502.50")
            other_id = seed_transaction(ticker="OTHERTKR", amount="-900.00")
            other_before = _row(other_id)

            cli_result = run_cli(
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
        with temp_ctx():
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli(
                cli_app,
                ["edit", str(txn_id), "--set", operation, "--force"],
            )

            assert_cli_success(cli_result)
            assert Decimal(str(_row(txn_id)[Column.Txn.PRICE])) == Decimal(expected)

    def test_arithmetic_is_exact(self, temp_ctx: TempContext) -> None:
        """Decimal maths avoids binary float drift."""
        with temp_ctx():
            ensure_data_exists()
            txn_id = seed_transaction(price="17.52")

            cli_result = run_cli(
                cli_app,
                ["edit", str(txn_id), "--set", "Price*=10", "--force"],
            )

            assert_cli_success(cli_result)
            assert Decimal(str(_row(txn_id)[Column.Txn.PRICE])) == Decimal("175.2")

    def test_batch_arithmetic_by_query_terms(self, temp_ctx: TempContext) -> None:
        """Query terms apply the same operation to every matched row."""
        with temp_ctx():
            ensure_data_exists()
            first = seed_transaction(price="10", amount="-100.00")
            second = seed_transaction(price="20", amount="-200.00")

            cli_result = run_cli(
                cli_app,
                ["edit", TICKER, "--set", "Price*=10", "--force"],
            )

            assert_cli_success(cli_result)
            assert Decimal(str(_row(first)[Column.Txn.PRICE])) == Decimal(100)
            assert Decimal(str(_row(second)[Column.Txn.PRICE])) == Decimal(200)

    def test_split_migration_scenario(self, temp_ctx: TempContext) -> None:
        """A manually split-adjusted history is un-adjusted, end to end."""
        with temp_ctx():
            ensure_data_exists()
            # History that was manually split-adjusted: price /10, units *10.
            txn_id = seed_transaction(
                ticker="SPLITCO",
                date="2025-08-01",
                price="12.34",
                units="100",
                amount="-1234.00",
            )
            split = run_cli(
                cli_app,
                [
                    "add",
                    "--type",
                    "SPLIT",
                    "--date",
                    "2025-08-10",
                    "--account",
                    ACCOUNT,
                    "--currency",
                    Currency.USD.value,
                    "--symbol",
                    "SPLITCO",
                    "--price",
                    "1",
                    "--units",
                    "10",
                ],
            )
            assert_cli_success(split)

            cli_result = run_cli(
                cli_app,
                [
                    "edit",
                    "SPLITCO",
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


_REFUSED_SETS = [
    pytest.param(
        ["Ticker*=2"],
        "not a numeric field",
        id="arithmetic-on-a-text-field",
    ),
    pytest.param(["justakey"], "Expected Field=VALUE", id="set-without-an-equals"),
    pytest.param(["Prcie=1"], "Unknown field 'Prcie'", id="a-typo-invents-nothing"),
    pytest.param(
        ["Price=1", "Price=2"],
        "was given more than once",
        id="a-repeat-is-not-last-wins",
    ),
    pytest.param(["TxnId=5"], "cannot be edited directly", id="protected-column"),
    pytest.param(["Price/=0"], "Cannot divide", id="division-by-zero"),
    pytest.param(["Price*=abc"], "is not a number", id="non-numeric-operand"),
    pytest.param(["TxnDate=notadate"], "is not a valid date", id="unparseable-date"),
    pytest.param(
        ["Amount="],
        "would produce invalid transaction(s)",
        id="clearing-a-required-field",
    ),
    # The engine converts USD and CAD only; a euro row would stop every replay.
    pytest.param(
        ["$=EUR"],
        "would produce invalid transaction(s)",
        id="a-currency-the-engine-cannot-convert",
    ),
]


class TestEditValidation:
    """Failures that abort the edit before anything is written."""

    @pytest.mark.parametrize(("spec", "message"), _REFUSED_SETS)
    def test_an_invalid_set_is_refused_and_writes_nothing(
        self,
        temp_ctx: TempContext,
        spec: list[str],
        message: str,
    ) -> None:
        """Refuse invalid edits. Must leave the row untouched."""
        with temp_ctx():
            txn_id = seed_transaction()
            before = _row(txn_id)
            flags = [flag for value in spec for flag in ("--set", value)]

            cli_result = run_cli(
                cli_app,
                ["edit", str(txn_id), *flags, "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output(message, cli_result)
            assert _row(txn_id).equals(before)


class TestEditDuplicates:
    """The CLI's handling of no-op and duplicating edits."""

    def test_no_op_edit_reports_no_changes(self, temp_ctx: TempContext) -> None:
        """Setting a field to the value it already has is not a duplicate."""
        with temp_ctx():
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli(
                cli_app,
                ["edit", str(txn_id), "--set", "Price=150.25", "--force"],
            )

            assert_cli_success(cli_result)
            assert_in_output("No changes to apply.", cli_result)

    def test_edit_onto_another_row_prompts(self, temp_ctx: TempContext) -> None:
        """An edit that would duplicate an existing row asks first."""
        with temp_ctx():
            ensure_data_exists()
            target = seed_transaction(price="150.25", amount="-1502.50")
            other = seed_transaction(price="999.99", amount="-1502.50")
            before = _row(other)

            cli_result = run_cli(
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
        with temp_ctx():
            ensure_data_exists()
            seed_transaction(price="150.25", amount="-1502.50")
            other = seed_transaction(price="999.99", amount="-1502.50")

            cli_result = run_cli(
                cli_app,
                ["edit", str(other), "--set", "Price=150.25", "--force"],
            )

            assert_cli_success(cli_result)
            assert Decimal(str(_row(other)[Column.Txn.PRICE])) == Decimal("150.25")


class TestEditSigns:
    """Sign normalization applies to edits, as it does to imports."""

    def test_buy_amount_is_forced_negative(self, temp_ctx: TempContext) -> None:
        """A positive Amount on a BUY is corrected on the way in."""
        with temp_ctx():
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli(
                cli_app,
                ["edit", str(txn_id), "--set", "Amount=3000", "--force"],
            )

            assert_cli_success(cli_result)
            row = _row(txn_id)
            assert row[Column.Txn.ACTION] == Action.BUY
            assert Decimal(str(row[Column.Txn.AMOUNT])) == Decimal(-3000)


class TestEditFlow:
    """Preview, confirmation, and side effects."""

    def test_preview_shows_the_value_being_replaced(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """The pending change shows the stored value, which was not typed in."""
        with temp_ctx():
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli(
                cli_app,
                ["edit", str(txn_id), "--set", "Price=175.20", "--dry-run"],
            )

            assert_cli_success(cli_result)
            assert_in_output("150.25", cli_result)

    def test_dry_run_writes_nothing(self, temp_ctx: TempContext) -> None:
        """--dry-run previews without writing."""
        with temp_ctx():
            ensure_data_exists()
            txn_id = seed_transaction()
            before = _row(txn_id)

            cli_result = run_cli(
                cli_app,
                ["edit", str(txn_id), "--set", "Price=175.20", "--dry-run"],
            )

            assert_cli_success(cli_result)
            assert_in_output("Dry run - nothing was written to the folio.", cli_result)
            assert _row(txn_id).equals(before)

    def test_declining_writes_nothing(self, temp_ctx: TempContext) -> None:
        """Answering no leaves the transaction alone."""
        with temp_ctx():
            ensure_data_exists()
            txn_id = seed_transaction()
            before = _row(txn_id)

            cli_result = run_cli(
                cli_app,
                ["edit", str(txn_id), "--set", "Price=175.20"],
                user_input="n\n",
            )

            assert_cli_success(cli_result)
            assert_in_output("No transactions edited.", cli_result)
            assert _row(txn_id).equals(before)

    def test_confirming_applies(
        self,
        temp_ctx: TempContext,
        audit_trail: list[str],
    ) -> None:
        """Answering yes writes the edit, logged as a diff with the old and new row."""
        with temp_ctx():
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli(
                cli_app,
                ["edit", str(txn_id), "--set", "Price=175.20"],
                user_input="y\n",
            )

            assert_cli_success(cli_result)
            assert Decimal(str(_row(txn_id)[Column.Txn.PRICE])) == Decimal("175.20")

        assert audit_trail[0] == (
            f"EDIT TXNS TxnIds {txn_id} SET Price=175.20 (folio edit)"
        )
        assert audit_trail[1].startswith("  UPDATE 1 transaction(s); ")
        assert audit_trail[2].startswith(f"    ~ TxnId {txn_id}: Price ")
        assert audit_trail[2].endswith(" -> 175.20")
        assert audit_trail[3].startswith(f"      - TxnId={txn_id}|")
        assert audit_trail[4].startswith(f"      + TxnId={txn_id}|")
        assert audit_trail[5] == "DONE 1 updated"

    @pytest.mark.real_parquet_export
    def test_edit_exports_to_parquet(self, temp_ctx: TempContext) -> None:
        """A successful edit refreshes the transactions parquet file."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli(
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

    def test_an_edit_short_of_its_match_closes_failed(
        self,
        temp_ctx: TempContext,
        audit_trail: list[str],
    ) -> None:
        """Rows that matched but were not written fail the command and its entry."""
        with temp_ctx():
            ensure_data_exists()
            txn_id = seed_transaction()

            with patch.object(txns, "update", return_value=0):
                cli_result = run_cli(
                    cli_app,
                    ["edit", str(txn_id), "--set", "Price=175.20", "--force"],
                )

        assert cli_result.exit_code == 1
        assert audit_trail[-3] == "FAILED: 1 transaction(s) matched but 0 updated"


class TestEditSelection:
    """Selection guards, shared with `folio delete`."""

    def test_unknown_txn_id_fails(self, temp_ctx: TempContext) -> None:
        """An unknown TxnId is an error."""
        with temp_ctx():
            ensure_data_exists()

            cli_result = run_cli(
                cli_app,
                ["edit", "999999", "--set", "Price=1", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("No transaction with TxnId 999999.", cli_result)

    def test_no_matches_fails(self, temp_ctx: TempContext) -> None:
        """Query terms matching nothing are an error."""
        with temp_ctx():
            ensure_data_exists()

            cli_result = run_cli(
                cli_app,
                ["edit", "ticker:NOSUCHTICKER", "--set", "Price=1", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("No transactions matched", cli_result)

    def test_unbounded_selection_is_refused(self, temp_ctx: TempContext) -> None:
        """A selection with no filters cannot rewrite the whole folio."""
        with temp_ctx():
            ensure_data_exists()

            cli_result = run_cli(
                cli_app,
                ["edit", "sort:Amount", "--set", "Price=1", "--force"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("Refusing to edit every transaction", cli_result)
