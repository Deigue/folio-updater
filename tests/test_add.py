"""Tests for the add command."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from cli.main import app as cli_app
from datagen import ensure_data_exists
from db import get_columns, get_connection
from domain import TORONTO_TZ, Action, Column, Currency, Table

from .helpers.cli import (
    assert_cli_success,
    assert_in_output,
    assert_not_in_output,
    run_cli_with_config,
)
from .helpers.folio import ticker_txns, txn_total
from .helpers.seed import ACCOUNT, TICKER, TXN_DATE

if TYPE_CHECKING:
    from .helpers.context import TempContext


def _add_args(
    action: str = "BUY",
    currency: Currency = Currency.USD,
    amount: str = "-1502.50",
    price: str | None = "150.25",
    units: str | None = "10",
) -> list[str]:
    """Build a fully specified `add` command line, so nothing is prompted for.

    Args:
        action: The --type given.
        currency: The --currency given.
        amount: The --amount given.
        price: The --price given, or None to leave it off.
        units: The --units given, or None to leave it off.

    Returns:
        The argv for `run_cli_with_config`.
    """
    args = [
        "add",
        "--type",
        action,
        "--date",
        TXN_DATE,
        "--account",
        ACCOUNT,
        "--currency",
        currency.value,
        "--symbol",
        TICKER,
        "--amount",
        amount,
    ]
    if price is not None:
        args += ["--price", price]
    if units is not None:
        args += ["--units", units]
    return args


BUY_ARGS = _add_args()

pytestmark = pytest.mark.usefixtures("quiet_logging")


def _added_txn(ticker: str = TICKER) -> pd.Series:
    """Fetch the single transaction added by a test, by ticker."""
    rows = ticker_txns(ticker)
    assert len(rows) == 1, f"Expected exactly one {ticker} transaction, got {len(rows)}"
    return rows.iloc[0]


class TestAddSuccess:
    """Successful add scenarios."""

    def test_add_buy_fully_specified(self, temp_ctx: TempContext) -> None:
        """A fully specified BUY is added without any prompting."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            before = txn_total()

            cli_result = run_cli_with_config(ctx.config, cli_app, BUY_ARGS)

            assert_cli_success(cli_result)
            assert txn_total() == before + 1
            row = _added_txn()
            assert row[Column.Txn.ACTION] == Action.BUY
            assert row[Column.Txn.TXN_DATE] == TXN_DATE
            assert row[Column.Txn.ACCOUNT] == ACCOUNT
            assert row[Column.Txn.CURRENCY] == Currency.USD
            assert float(row[Column.Txn.PRICE]) == pytest.approx(150.25)
            assert float(row[Column.Txn.UNITS]) == pytest.approx(10)
            assert float(row[Column.Txn.AMOUNT]) == pytest.approx(-1502.50)

    def test_add_action_alias_is_normalized(self, temp_ctx: TempContext) -> None:
        """An action alias is normalized the same way imports normalize it."""
        with temp_ctx() as ctx:
            ensure_data_exists()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                _add_args("div", Currency.CAD, "12.34", price=None, units=None),
            )

            assert_cli_success(cli_result)
            assert _added_txn()[Column.Txn.ACTION] == Action.DIVIDEND

    def test_add_fee_and_custom_column(self, temp_ctx: TempContext) -> None:
        """--fee and --set populate their columns, adding new ones as needed."""
        with temp_ctx() as ctx:
            ensure_data_exists()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                [*BUY_ARGS, "--fee", "4.95", "--set", "Notes=manual entry"],
            )

            assert_cli_success(cli_result)
            with get_connection() as conn:
                assert "Notes" in get_columns(conn, Table.TXNS)
            row = _added_txn()
            assert float(row[Column.Txn.FEE]) == pytest.approx(4.95)
            assert row["Notes"] == "manual entry"

    @pytest.mark.real_parquet_export
    def test_add_exports_to_parquet(self, temp_ctx: TempContext) -> None:
        """A successful add refreshes the transactions parquet file."""
        with temp_ctx() as ctx:
            ensure_data_exists()

            cli_result = run_cli_with_config(ctx.config, cli_app, BUY_ARGS)

            assert_cli_success(cli_result)
            exported = pd.read_parquet(ctx.config.txn_parquet, engine="fastparquet")
            assert TICKER in set(exported[Column.Txn.TICKER])


class TestAddPrompting:
    """Prompting is driven by the action's validation rules."""

    def test_split_prompts_price_units_not_amount(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """SPLIT prompts for Price/Units/Ticker and never for Amount."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            # TxnDate, Account, Currency, Ticker, Price, Units
            answers = f"{TXN_DATE}\n{ACCOUNT}\n{Currency.USD.value}\n{TICKER}\n1\n10\n"

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["add", "--type", "SPLIT"],
                user_input=answers,
            )

            assert_cli_success(cli_result)
            # Prompt text is echoed on stdout; the rich tables that mention
            # every column land in plain_output instead.
            prompts = cli_result.stdout
            assert "units BEFORE the split" in prompts
            assert "units AFTER the split" in prompts
            assert "Amount" not in prompts

            row = _added_txn()
            assert row[Column.Txn.ACTION] == Action.SPLIT
            assert float(row[Column.Txn.PRICE]) == pytest.approx(1)
            assert float(row[Column.Txn.UNITS]) == pytest.approx(10)

    def test_roc_prompts_amount_not_price_units(self, temp_ctx: TempContext) -> None:
        """ROC prompts for Amount/Ticker and never for Price or Units."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            # TxnDate, Account, Currency, Ticker, Amount
            answers = f"{TXN_DATE}\n{ACCOUNT}\n{Currency.CAD.value}\n{TICKER}\n25.00\n"

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["add", "--type", "ROC"],
                user_input=answers,
            )

            assert_cli_success(cli_result)
            prompts = cli_result.stdout
            assert "Amount" in prompts
            assert "Price" not in prompts
            assert "Units" not in prompts

            row = _added_txn()
            assert row[Column.Txn.ACTION] == Action.ROC
            assert float(row[Column.Txn.AMOUNT]) == pytest.approx(25.00)

    def test_date_prompt_defaults_to_today(self, temp_ctx: TempContext) -> None:
        """Accepting the TxnDate prompt default uses today's date."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            # Empty first answer accepts the default date.
            answers = f"\n{ACCOUNT}\n{Currency.CAD.value}\n{TICKER}\n25.00\n"

            before = datetime.now(TORONTO_TZ).strftime("%Y-%m-%d")
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["add", "--type", "ROC"],
                user_input=answers,
            )
            after = datetime.now(TORONTO_TZ).strftime("%Y-%m-%d")

            assert_cli_success(cli_result)
            # Either side of midnight, should the run straddle it.
            assert _added_txn()[Column.Txn.TXN_DATE] in {before, after}

    def test_invalid_action_reprompts(self, temp_ctx: TempContext) -> None:
        """An unrecognized action is rejected and asked for again."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            answers = (
                f"ROC\n{TXN_DATE}\n{ACCOUNT}\n{Currency.CAD.value}\n{TICKER}\n25.00\n"
            )

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["add", "--type", "NOTANACTION"],
                user_input=answers,
            )

            assert_cli_success(cli_result)
            assert_in_output("Unknown type", cli_result)
            assert _added_txn()[Column.Txn.ACTION] == Action.ROC


class TestAddValidation:
    """Invalid input is rejected without touching the database."""

    @pytest.mark.parametrize(
        ("option", "value", "expected"),
        [
            ("--date", "not-a-date", "INVALID TxnDate"),
            ("--amount", "twelve", "INVALID Amount"),
            ("--currency", "GBP", "INVALID $"),
            # Known to the app, but not convertible: it would stop every replay.
            ("--currency", "EUR", "INVALID $"),
            ("--symbol", "BAD TICKER", "INVALID Ticker"),
        ],
    )
    def test_invalid_field_is_rejected(
        self,
        temp_ctx: TempContext,
        option: str,
        value: str,
        expected: str,
    ) -> None:
        """A malformed field aborts the add and reports the rejection reason."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            before = txn_total()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                [*BUY_ARGS, option, value],
            )

            assert cli_result.exit_code == 1
            assert_in_output(expected, cli_result)
            assert txn_total() == before

    def test_empty_account_is_rejected(self, temp_ctx: TempContext) -> None:
        """An empty Account is refused cleanly instead of raising IntegrityError."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            before = txn_total()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                [*BUY_ARGS, "--account", ""],
            )

            assert cli_result.exit_code == 1
            assert_in_output("Database rejected the transaction", cli_result)
            assert txn_total() == before

    def test_invalid_set_value_is_rejected(self, temp_ctx: TempContext) -> None:
        """A --set argument that is not KEY=VALUE aborts the add."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            before = txn_total()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                [*BUY_ARGS, "--set", "justakey"],
            )

            assert cli_result.exit_code == 1
            assert_in_output("Expected KEY=VALUE", cli_result)
            assert txn_total() == before

    def test_dry_run_writes_nothing(self, temp_ctx: TempContext) -> None:
        """--dry-run previews the transaction without inserting it."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            before = txn_total()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                [*BUY_ARGS, "--dry-run"],
            )

            assert_cli_success(cli_result)
            assert_in_output("Dry run", cli_result)
            assert txn_total() == before


class TestAddDuplicates:
    """Clashing transactions require explicit approval."""

    def test_duplicate_declined_is_not_added(self, temp_ctx: TempContext) -> None:
        """Declining the duplicate prompt leaves the folio untouched."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            assert_cli_success(run_cli_with_config(ctx.config, cli_app, BUY_ARGS))
            before = txn_total()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                BUY_ARGS,
                user_input="n\n",
            )

            assert_cli_success(cli_result)
            assert_in_output("matches 1 existing transaction", cli_result)
            assert_in_output("Transaction not added", cli_result)
            assert txn_total() == before

    def test_duplicate_confirmed_is_added(self, temp_ctx: TempContext) -> None:
        """Confirming the duplicate prompt adds the transaction anyway."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            assert_cli_success(run_cli_with_config(ctx.config, cli_app, BUY_ARGS))
            before = txn_total()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                BUY_ARGS,
                user_input="y\n",
            )

            assert_cli_success(cli_result)
            assert txn_total() == before + 1

    def test_force_skips_the_duplicate_prompt(self, temp_ctx: TempContext) -> None:
        """--force adds a duplicate without prompting."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            assert_cli_success(run_cli_with_config(ctx.config, cli_app, BUY_ARGS))
            before = txn_total()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                [*BUY_ARGS, "--force"],
            )

            assert_cli_success(cli_result)
            assert_not_in_output("intentional duplicate", cli_result)
            assert txn_total() == before + 1


class TestAddSigns:
    """Sign rules apply to manually added transactions, as they do to imports."""

    def test_buy_amount_is_forced_negative(self, temp_ctx: TempContext) -> None:
        """A BUY entered with a positive amount is stored as cash out."""
        with temp_ctx() as ctx:
            ensure_data_exists()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                _add_args(amount="1502.50"),
            )

            assert_cli_success(cli_result)
            row = _added_txn()
            assert float(row[Column.Txn.AMOUNT]) == pytest.approx(-1502.50)
            assert float(row[Column.Txn.UNITS]) == pytest.approx(10)

    def test_sell_units_are_forced_negative(self, temp_ctx: TempContext) -> None:
        """A SELL entered with positive units is stored as units disposed."""
        with temp_ctx() as ctx:
            ensure_data_exists()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                _add_args("SELL"),
            )

            assert_cli_success(cli_result)
            row = _added_txn()
            assert float(row[Column.Txn.AMOUNT]) == pytest.approx(1502.50)
            assert float(row[Column.Txn.UNITS]) == pytest.approx(-10)


class TestAddSettlement:
    """Settlement dates are calculated the same way imports calculate them."""

    def test_buy_settles_on_a_later_business_day(self, temp_ctx: TempContext) -> None:
        """A BUY gets a calculated settlement date after the trade date."""
        with temp_ctx() as ctx:
            ensure_data_exists()

            assert_cli_success(run_cli_with_config(ctx.config, cli_app, BUY_ARGS))

            row = _added_txn()
            assert int(row[Column.Txn.SETTLE_CALCULATED]) == 1
            assert row[Column.Txn.SETTLE_DATE] > TXN_DATE

    def test_roc_settles_same_day(self, temp_ctx: TempContext) -> None:
        """ROC is a same-day settling action."""
        with temp_ctx() as ctx:
            ensure_data_exists()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                _add_args("ROC", Currency.CAD, "25.00", price=None, units=None),
            )

            assert_cli_success(cli_result)
            assert _added_txn()[Column.Txn.SETTLE_DATE] == TXN_DATE
