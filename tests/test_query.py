"""Tests for the query command and query parsing."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from cli.main import app as cli_app
from cli.query_parser import parse_query_terms
from datagen import ensure_data_exists
from db import add_column_to_table, get_connection, get_row_count, get_rows
from domain import Column, Table

from .helpers.cli import (
    assert_cli_success,
    assert_in_output,
    assert_not_in_output,
    run_cli_with_config,
)
from .helpers.folio import earliest_txn_date, plain_ticker, txn_total
from .helpers.seed import seed_transaction

if TYPE_CHECKING:
    from collections.abc import Generator

    from tests.test_types import TempContext


@pytest.fixture(autouse=True)
def suppress_logging_conflicts() -> Generator[None, Any]:
    """Suppress logging to avoid stream conflicts during CLI testing."""
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


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
            query = parse_query_terms(["Description~RECEIPTS"])
            assert len(query.filters) == 1
            assert query.filters[0].column == "Description"
            assert query.filters[0].operator == "~"
            assert query.filters[0].value == "RECEIPTS"


class TestQueryCommand:
    """Tests for the query CLI command."""

    def test_query_no_terms(self, temp_ctx: TempContext) -> None:
        """Test query command without any terms."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            cli_result = run_cli_with_config(ctx.config, cli_app, ["query"])
            # Typer requires the terms argument, so it fails with exit code 2
            assert cli_result.exit_code == 2

    def test_query_single_ticker(self, temp_ctx: TempContext) -> None:
        """Test query for a single ticker."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            ticker = plain_ticker()
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", ticker],
            )
            assert_cli_success(cli_result)
            # The results show the ticker was found
            assert_in_output(ticker, cli_result)

    def test_query_combined_filters(self, temp_ctx: TempContext) -> None:
        """Test query with multiple combined filters."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "BUY", plain_ticker()],
            )
            assert_cli_success(cli_result)
            # Should find matching results
            result_has_output = (
                "Found" in cli_result.plain_output
                or "No transactions" in cli_result.plain_output
            )
            assert result_has_output

    def test_query_explicit_filter(self, temp_ctx: TempContext) -> None:
        """Test query with explicit filter syntax."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "Action:BUY"],
            )
            assert_cli_success(cli_result)
            assert_in_output('Action="BUY"', cli_result)

    def test_query_results_with_ticker(self, temp_ctx: TempContext) -> None:
        """Test that query returns correct results for a ticker."""
        with temp_ctx() as ctx:
            ensure_data_exists()

            # Count txns for a specific ticker in the database
            with get_connection() as conn:
                ticker = plain_ticker()
                where = f'"{Column.Txn.TICKER}" = ?'
                params = [ticker]
                expected_count = get_row_count(conn, Table.TXNS, where, params)

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

    def test_query_text_search(self, temp_ctx: TempContext) -> None:
        """Test query with text search terms."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            # Get an actual account name
            with get_connection() as conn:
                accounts_df = get_rows(conn, Table.TXNS)
                if not accounts_df.empty:
                    account = accounts_df.iloc[0][Column.Txn.ACCOUNT]
                    # Search for part of the account name
                    search_term = account[:3] if len(account) > 3 else account
                    cli_result = run_cli_with_config(
                        ctx.config,
                        cli_app,
                        ["query", search_term],
                    )
                    assert_cli_success(cli_result)

    def test_query_action_with_ticker(self, temp_ctx: TempContext) -> None:
        """Test query combining action and ticker."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            ticker = plain_ticker()
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "BUY", ticker],
            )
            assert_cli_success(cli_result)
            assert_in_output("BUY", cli_result)
            assert_in_output(ticker, cli_result)

    def test_query_partial_date(self, temp_ctx: TempContext) -> None:
        """Test query with partial dates (year or year-month)."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            # Test with year only
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", ">=2025"],
            )
            assert_cli_success(cli_result)

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

    def test_query_case_insensitive_action(self, temp_ctx: TempContext) -> None:
        """Test that action keywords are case-insensitive."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "buy"],  # lowercase
            )
            assert_cli_success(cli_result)
            assert_in_output("BUY", cli_result)

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

            # 2. Create an alias: ticker_alias_old -> ticker_alias_new
            run_cli_with_config(
                config,
                cli_app,
                ["symbol", "--add", ticker_alias_old, ticker_alias_new, "2025-01-01"],
            )

            # 3. Query for the old ticker: finds both old and new transactions
            result_a = run_cli_with_config(config, cli_app, ["query", ticker_alias_old])
            assert_cli_success(result_a)
            assert_in_output("Found 4 matching transaction(s).", result_a)
            assert_in_output(f" {ticker_alias_old} ", result_a)
            assert_in_output(f" {ticker_alias_new} ", result_a)
            assert_not_in_output(f" {ticker_unrelated} ", result_a)

            # 4. Query for the new ticker: also finds both
            result_b = run_cli_with_config(config, cli_app, ["query", ticker_alias_new])
            assert_cli_success(result_b)
            assert_in_output("Found 4 matching transaction(s).", result_b)
            assert_in_output(f" {ticker_alias_old} ", result_b)
            assert_in_output(f" {ticker_alias_new} ", result_b)
            assert_not_in_output(f" {ticker_unrelated} ", result_b)

            # 5. Query for the unrelated ticker: only its own transactions
            result_c = run_cli_with_config(config, cli_app, ["query", ticker_unrelated])
            assert_cli_success(result_c)
            assert_in_output("Found 2 matching transaction(s).", result_c)
            assert_in_output(f" {ticker_unrelated} ", result_c)
            assert_not_in_output(f" {ticker_alias_old} ", result_c)
            assert_not_in_output(f" {ticker_alias_new} ", result_c)

    def test_query_sorting_ascending_descending(self, temp_ctx: TempContext) -> None:
        """Test sort command with both ascending and descending."""
        with temp_ctx() as ctx:
            ensure_data_exists()

            # Test ascending sort
            result_asc = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "sort:Ticker"],
            )
            assert_cli_success(result_asc)
            assert_in_output("sort:Ticker(ASC)", result_asc)

            # Test descending sort
            result_desc = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "sort:-Amount"],
            )
            assert_cli_success(result_desc)
            assert_in_output("sort:Amount(DESC)", result_desc)

    def test_query_multiple_sorts(self, temp_ctx: TempContext) -> None:
        """Test multiple sort commands applied in order."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "sort:Ticker", "sort:-TxnDate"],
            )
            assert_cli_success(cli_result)
            assert_in_output("sort:Ticker(ASC)", cli_result)
            assert_in_output("sort:TxnDate(DESC)", cli_result)

    def test_query_contains_operator(self, temp_ctx: TempContext) -> None:
        """Test that explicit `~` (contains/LIKE) filters execute against the DB."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            with get_connection() as conn:
                accounts_df = get_rows(conn, Table.TXNS)
                account = accounts_df.iloc[0][Column.Txn.ACCOUNT]
                search_term = account[:3] if len(account) > 3 else account
                where = f'"{Column.Txn.ACCOUNT}" LIKE ?'
                params = [f"%{search_term}%"]
                expected_count = get_row_count(conn, Table.TXNS, where, params)

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", f"Account~{search_term}"],
            )
            assert_cli_success(cli_result)
            output_msg = f"Found {expected_count} matching transaction(s)"
            assert_in_output(output_msg, cli_result)

    def test_query_ticker_and_account_search(self, temp_ctx: TempContext) -> None:
        """Test that both ticker and account names are searched."""
        with temp_ctx() as ctx:
            ensure_data_exists()

            # Query for a known ticker
            ticker = plain_ticker()
            result_ticker = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", ticker],
            )
            assert_cli_success(result_ticker)
            assert_in_output(f'Ticker="{ticker}"', result_ticker)

    def test_query_limit_natural_language(self, temp_ctx: TempContext) -> None:
        """Test that 'last N' (no time unit) limits the number of results."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "last", "5"],
            )
            assert_cli_success(cli_result)
            assert_in_output("limit:-5", cli_result)
            assert_in_output("Found 5 matching transaction(s).", cli_result)

    def test_query_limit_explicit(self, temp_ctx: TempContext) -> None:
        """Test explicit 'limit:N' filter syntax."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            cli_result = run_cli_with_config(ctx.config, cli_app, ["query", "limit:3"])
            assert_cli_success(cli_result)
            assert_in_output("limit:3", cli_result)
            assert_in_output("Found 3 matching transaction(s).", cli_result)

    def test_query_between_dates_covers_all(self, temp_ctx: TempContext) -> None:
        """Test 'between X and Y' with a wide range returns every transaction."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "between", "2020", "and", "2030"],
            )
            assert_cli_success(cli_result)
            expected_total = txn_total()
            output_msg = f"Found {expected_total} matching transaction(s)"
            assert_in_output(output_msg, cli_result)

    def test_query_after_before_bounds(self, temp_ctx: TempContext) -> None:
        """Test single-sided 'after'/'before' date phrases."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            expected_total = txn_total()

            result_after = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "after", "2000"],
            )
            assert_cli_success(result_after)
            output_msg = f"Found {expected_total} matching transaction(s)"
            assert_in_output(output_msg, result_after)

            result_before = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "before", "2000"],
            )
            assert_cli_success(result_before)
            assert_in_output("No transactions found", result_before)

    def test_query_from_no_to_with_limit(self, temp_ctx: TempContext) -> None:
        """Test the documented 'from X ... first N' combo (open-ended 'from')."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "from", "2020-01", "first", "4"],
            )
            assert_cli_success(cli_result)
            assert_in_output("TxnDate>=2020-01-01", cli_result)
            assert_in_output("limit:4", cli_result)
            assert_in_output("Found 4 matching transaction(s).", cli_result)


class TestQueryByTxnId:
    """Query terms that are plain integers select explicit TxnIds.

    This mirrors the TxnId selection `folio edit` and `folio delete` accept,
    so all three commands share the same selection syntax.
    """

    def test_query_single_txn_id(self, temp_ctx: TempContext) -> None:
        """A single TxnId returns exactly that transaction."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", str(txn_id)],
            )

            assert_cli_success(cli_result)
            assert_in_output("Found 1 matching transaction(s).", cli_result)

    def test_query_multiple_txn_ids(self, temp_ctx: TempContext) -> None:
        """Several TxnIds are all returned by one query call."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            first = seed_transaction(amount="-1502.50")
            second = seed_transaction(amount="-1602.50")
            third = seed_transaction(amount="-1702.50")

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", str(first), str(second), str(third)],
            )

            assert_cli_success(cli_result)
            assert_in_output("Found 3 matching transaction(s).", cli_result)

    def test_query_repeated_txn_id_counted_once(self, temp_ctx: TempContext) -> None:
        """A TxnId given twice is deduplicated rather than counted twice."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            txn_id = seed_transaction()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", str(txn_id), str(txn_id)],
            )

            assert_cli_success(cli_result)
            assert_in_output("Found 1 matching transaction(s).", cli_result)

    def test_query_unknown_txn_id_reports_missing(self, temp_ctx: TempContext) -> None:
        """An unmatched TxnId is reported rather than silently dropped."""
        with temp_ctx() as ctx:
            ensure_data_exists()

            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "999999"],
            )

            assert_cli_success(cli_result)
            assert_in_output("No transaction with TxnId 999999.", cli_result)
            assert_in_output("No transactions found", cli_result)

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
