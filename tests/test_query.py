"""Tests for the query command, and the parser against a live schema."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest

from cli.main import app as cli_app
from cli.query_parser import parse_query_terms
from cli.selection import load_vocabulary
from datagen import ensure_data_exists
from db import add_column_to_table, get_connection, get_row_count
from domain import Column, Table

from .helpers.cli import (
    assert_cli_success,
    assert_in_output,
    assert_not_in_output,
    run_cli_with_config,
)
from .helpers.folio import earliest_txn_date, plain_ticker, ticker_rows
from .helpers.seed import seed_transaction

if TYPE_CHECKING:
    from .helpers.context import TempContext


pytestmark = pytest.mark.usefixtures("quiet_logging")


class TestQueryParserAgainstLiveSchema:
    """Parser behaviour that depends on the real database schema.

    The parsing rules themselves live in test_query_parser.py with the reference
    lookups stubbed. This case stays wired to a genuine db so the live-column
    lookup path is exercised end to end against a column added at runtime.
    """

    def test_parse_optional_column_filter(self, temp_ctx: TempContext) -> None:
        """Test explicit filters validate against live (optional) Txns columns."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            with get_connection() as conn:
                add_column_to_table(conn, Table.TXNS, "Description", "TEXT")
            query = parse_query_terms(["Description~RECEIPTS"], load_vocabulary())
            assert len(query.filters) == 1
            assert query.filters[0].column == "Description"
            assert query.filters[0].operator == "~"
            assert query.filters[0].value == "RECEIPTS"


class TestQueryCommand:
    """The `query` command end to end: terms in, the right count reported.

    How a term parses is tested in test_query_parser.py, and which rows a filter,
    search or sort returns in test_selection.py. These check the command wires
    the two together and reports what it found.
    """

    def test_query_results_with_ticker(self, temp_ctx: TempContext) -> None:
        """Test that query returns correct results for a ticker."""
        with temp_ctx() as ctx:
            ensure_data_exists()

            ticker = plain_ticker()
            expected_count = ticker_rows(ticker)

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", ticker],
            )
            assert_cli_success(cli_result)
            output_msg = f"Found {expected_count} matching transaction(s)"
            assert_in_output(output_msg, cli_result)

    def test_query_no_results(self, temp_ctx: TempContext) -> None:
        """Test query that returns no results."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "NONEXISTENT_TICKER"],
            )
            assert_cli_success(cli_result)
            assert_in_output("No transactions found", cli_result)

    def test_query_against_empty_database(self, temp_ctx: TempContext) -> None:
        """Test query fails gracefully when the Txns table doesn't exist yet."""
        with temp_ctx() as ctx:
            # Deliberately skip ensure_data_exists() so no tables are created.
            ctx.config.db_path.parent.mkdir(parents=True, exist_ok=True)
            sqlite3.connect(ctx.config.db_path).close()
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "AAPL"],
            )
            assert_cli_success(cli_result)
            assert_in_output("No transactions found", cli_result)

    def test_query_month_year_phrase_matches_exact_date_range(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """Test 'MONTH YEAR' (either word order) returns exactly that month's rows."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            target_month_start = date.fromisoformat(earliest_txn_date()).replace(day=1)
            target_year = target_month_start.year
            target_month = target_month_start.month
            month_prefix = f"{target_year}-{target_month:02d}"

            with get_connection() as conn:
                expected_count = get_row_count(
                    conn,
                    Table.TXNS,
                    f'"{Column.Txn.TXN_DATE}" LIKE ?',
                    [f"{month_prefix}-%"],
                )
            # Sanity check: the fixture actually has data in this month
            assert expected_count > 0

            month_name = target_month_start.strftime("%B").lower()
            found_message = f"Found {expected_count} matching transaction(s)."

            for terms in (
                [month_name, str(target_year)],
                [str(target_year), month_name],
            ):
                cli_result = run_cli_with_config(ctx.config, cli_app, ["query", *terms])
                assert_cli_success(cli_result)
                assert_in_output(found_message, cli_result)

            # Search for month outside of fixture bounds.
            prior_month_end = target_month_start - timedelta(days=1)
            prior_month_name = prior_month_end.strftime("%B").lower()
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", prior_month_name, str(prior_month_end.year)],
            )
            assert_cli_success(cli_result)
            assert_in_output("No transactions found", cli_result)

    def test_query_command_with_aliases(self, temp_ctx: TempContext) -> None:
        """Test that the query command correctly uses ticker aliases.

        This comprehensive test validates that:
        1. Aliasing a ticker makes both old and new tickers searchable
        2. Querying the old ticker finds both old and new ticker transactions
        3. Querying the new ticker finds both old and new ticker transactions
        4. Querying an unrelated ticker finds only its own transactions

        The renamed pair is seeded on top of the folio, so the counts do not
        depend on what the folio already holds.
        """
        with temp_ctx() as ctx:
            config = ctx.config
            ensure_data_exists()
            ticker_alias_old, ticker_alias_new, ticker_unrelated = (
                "OLDCO",
                "NEWCO",
                "OTHCO",
            )
            for ticker in (ticker_alias_old, ticker_alias_new, ticker_unrelated):
                seed_transaction(ticker=ticker, amount="-1502.50")
                seed_transaction(ticker=ticker, amount="-1602.50")

            # 1. Create an alias: ticker_alias_old -> ticker_alias_new
            run_cli_with_config(
                config,
                cli_app,
                ["symbol", "--add", ticker_alias_old, ticker_alias_new, "2025-01-01"],
            )

            # 2. Query for the old ticker: finds both old and new transactions
            result_a = run_cli_with_config(config, cli_app, ["query", ticker_alias_old])
            assert_cli_success(result_a)
            assert_in_output("Found 4 matching transaction(s).", result_a)
            assert_in_output(f" {ticker_alias_old} ", result_a)
            assert_in_output(f" {ticker_alias_new} ", result_a)
            assert_not_in_output(f" {ticker_unrelated} ", result_a)

            # 3. Query for the new ticker: also finds both
            result_b = run_cli_with_config(config, cli_app, ["query", ticker_alias_new])
            assert_cli_success(result_b)
            assert_in_output("Found 4 matching transaction(s).", result_b)
            assert_in_output(f" {ticker_alias_old} ", result_b)
            assert_in_output(f" {ticker_alias_new} ", result_b)
            assert_not_in_output(f" {ticker_unrelated} ", result_b)

            # 4. Query for the unrelated ticker: only its own transactions
            result_c = run_cli_with_config(config, cli_app, ["query", ticker_unrelated])
            assert_cli_success(result_c)
            assert_in_output("Found 2 matching transaction(s).", result_c)
            assert_in_output(f" {ticker_unrelated} ", result_c)
            assert_not_in_output(f" {ticker_alias_old} ", result_c)
            assert_not_in_output(f" {ticker_alias_new} ", result_c)


class TestQueryByTxnId:
    """Query terms that are plain integers select explicit TxnIds.

    This mirrors the TxnId selection `folio edit` and `folio delete` accept,
    so all three commands share the same selection syntax.
    """

    def test_query_mixed_ids_reports_missing_and_found(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """A mix of valid and unknown TxnIds still returns the ones that exist."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", str(txn_id), "999999"],
            )

            assert_cli_success(cli_result)
            assert_in_output("No transaction with TxnId 999999.", cli_result)
            assert_in_output("Found 1 matching transaction(s).", cli_result)
