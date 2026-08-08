"""Tests for the query command and query parsing."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, Any

import pytest

from cli import query_parser
from cli.main import app as cli_app
from cli.query_parser import parse_query_terms
from datagen import DEFAULT_TXN_COUNT, ensure_data_exists
from db import add_column_to_table, get_connection, get_row_count, get_rows
from db.formatters import ActionValidationRules
from utils import TORONTO_TZ
from utils.constants import DEFAULT_TICKERS, Action, Column, Table

from .helpers.cli import (
    assert_cli_success,
    assert_in_output,
    assert_not_in_output,
    run_cli_with_config,
)

if TYPE_CHECKING:
    from collections.abc import Generator

    from tests.test_types import TempContext


@pytest.fixture(autouse=True)
def suppress_logging_conflicts() -> Generator[None, Any]:
    """Suppress logging to avoid stream conflicts during CLI testing."""
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


# Calculate expected number of excluded transactions per ticker
# based on generate_transactions logic and ActionValidationRules
_actions = list(Action)
_expected_excluded_txns_per_ticker = 0
for i in range(DEFAULT_TXN_COUNT):
    _action = _actions[i % len(_actions)]
    _rules = ActionValidationRules.get_rules_for_action(_action.value)
    if Column.Txn.TICKER in _rules["optional_fields"]:
        _expected_excluded_txns_per_ticker += 1


class TestQueryParser:
    """Tests for the query parser logic."""

    def test_parse_ticker(self, temp_ctx: TempContext) -> None:
        """Test parsing a simple ticker."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms([DEFAULT_TICKERS[0]])
            assert len(query.filters) == 1
            assert query.filters[0].column == Column.Txn.TICKER
            assert query.filters[0].operator == ":"
            assert query.filters[0].value == DEFAULT_TICKERS[0]

    def test_parse_action(self, temp_ctx: TempContext) -> None:
        """Test parsing an action keyword."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["BUY"])
            assert len(query.filters) == 1
            assert query.filters[0].column == Column.Txn.ACTION
            assert query.filters[0].operator == ":"
            assert query.filters[0].value == "BUY"

    def test_parse_account(self, temp_ctx: TempContext) -> None:
        """Test parsing an account name."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            # Get an actual account from the database
            with get_connection() as conn:
                accounts_df = get_rows(conn, Table.TXNS)
                if not accounts_df.empty:
                    account = accounts_df.iloc[0][Column.Txn.ACCOUNT]
                    query = parse_query_terms([account])
                    assert len(query.filters) >= 1
                    # Check if account filter exists
                    account_filters = [
                        f for f in query.filters if f.column == Column.Txn.ACCOUNT
                    ]
                    assert len(account_filters) > 0
                    assert account_filters[0].value == account

    def test_parse_exact_date(self, temp_ctx: TempContext) -> None:
        """Test parsing an exact date."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["2025-01-15"])
            assert len(query.filters) == 1
            assert query.filters[0].column == Column.Txn.TXN_DATE
            assert query.filters[0].operator == ":"
            assert query.filters[0].value == "2025-01-15"

    def test_parse_month_day_year_phrase(self, temp_ctx: TempContext) -> None:
        """Test parsing 'MONTH DAY YEAR' (e.g. 'august 10 2024') as an exact date."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["august", "10", "2024"])
            assert len(query.filters) == 1
            assert query.filters[0].column == Column.Txn.TXN_DATE
            assert query.filters[0].operator == ":"
            assert query.filters[0].value == "2024-08-10"

    def test_parse_bare_month_name_is_not_a_date(self, temp_ctx: TempContext) -> None:
        """Test a bare month name with no day stays a text search (e.g. ticker MAY)."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["MAY"])
            assert not query.filters
            assert query.text_searches == ["MAY"]

    def test_parse_date_range(self, temp_ctx: TempContext) -> None:
        """Test parsing a date range (from:to)."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["2025-01-01:2025-12-31"])
            # Should have two filters for date range
            assert len(query.filters) == 2
            assert query.filters[0].column == Column.Txn.TXN_DATE
            assert query.filters[0].operator == ">="
            assert query.filters[1].column == Column.Txn.TXN_DATE
            assert query.filters[1].operator == "<="

    def test_parse_date_comparison(self, temp_ctx: TempContext) -> None:
        """Test parsing date comparison operators."""
        with temp_ctx() as _ctx:
            ensure_data_exists()

            # Test greater than
            query = parse_query_terms([">2025-01-01"])
            assert len(query.filters) == 1
            assert query.filters[0].operator == ">"
            assert query.filters[0].value == "2025-01-01"

            # Test greater than or equal
            query = parse_query_terms([">=2025-01-01"])
            assert len(query.filters) == 1
            assert query.filters[0].operator == ">="

            # Test less than
            query = parse_query_terms(["<2025-12-31"])
            assert len(query.filters) == 1
            assert query.filters[0].operator == "<"

            # Test less than or equal
            query = parse_query_terms(["<=2025-12-31"])
            assert len(query.filters) == 1
            assert query.filters[0].operator == "<="

    def test_parse_explicit_filter_exact(self, temp_ctx: TempContext) -> None:
        """Test parsing explicit filter with exact match operator."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["Action:BUY"])
            assert len(query.filters) == 1
            assert query.filters[0].column == Column.Txn.ACTION
            assert query.filters[0].operator == ":"
            assert query.filters[0].value == "BUY"

    def test_parse_explicit_filter_contains(self, temp_ctx: TempContext) -> None:
        """Test parsing explicit filter with contains operator."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["Account~TFSA"])
            assert len(query.filters) == 1
            assert query.filters[0].column == Column.Txn.ACCOUNT
            assert query.filters[0].operator == "~"
            assert query.filters[0].value == "TFSA"

    def test_parse_explicit_filter_comparison(self, temp_ctx: TempContext) -> None:
        """Test parsing explicit filter with comparison operators."""
        with temp_ctx() as _ctx:
            ensure_data_exists()

            query = parse_query_terms(["Amount>1000"])
            assert len(query.filters) == 1
            assert query.filters[0].column == Column.Txn.AMOUNT
            assert query.filters[0].operator == ">"
            assert query.filters[0].value == "1000"

            query = parse_query_terms(["Price<=150"])
            assert len(query.filters) == 1
            assert query.filters[0].column == Column.Txn.PRICE
            assert query.filters[0].operator == "<="
            assert query.filters[0].value == "150"

    def test_parse_sort_ascending(self, temp_ctx: TempContext) -> None:
        """Test parsing sort in ascending order."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["sort:Ticker"])
            assert len(query.sorts) == 1
            assert query.sorts[0].column == Column.Txn.TICKER
            assert query.sorts[0].direction == "asc"

    def test_parse_sort_descending(self, temp_ctx: TempContext) -> None:
        """Test parsing sort in descending order."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["sort:-Amount"])
            assert len(query.sorts) == 1
            assert query.sorts[0].column == Column.Txn.AMOUNT
            assert query.sorts[0].direction == "desc"

    def test_parse_multiple_sorts(self, temp_ctx: TempContext) -> None:
        """Test parsing multiple sort specifications."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["sort:Ticker", "sort:-TxnDate"])
            assert len(query.sorts) == 2
            assert query.sorts[0].column == Column.Txn.TICKER
            assert query.sorts[0].direction == "asc"
            assert query.sorts[1].column == Column.Txn.TXN_DATE
            assert query.sorts[1].direction == "desc"

    def test_parse_text_search(self, temp_ctx: TempContext) -> None:
        """Test parsing unrecognized terms as text searches."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["UNKNOWN_TERM"])
            assert len(query.text_searches) == 1
            assert query.text_searches[0] == "UNKNOWN_TERM"

    def test_parse_combined_query(self, temp_ctx: TempContext) -> None:
        """Test parsing a complex combined query."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(
                [
                    DEFAULT_TICKERS[0],
                    "BUY",
                    "2025-01-01:2025-12-31",
                    "sort:-Amount",
                ],
            )
            # Should have: ticker filter, action filter, 2 date filters
            assert len(query.filters) == 4
            assert len(query.sorts) == 1
            assert query.sorts[0].direction == "desc"

    def test_parse_currency_keyword(self, temp_ctx: TempContext) -> None:
        """Test parsing a bare currency keyword (exact match, like Action)."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["USD"])
            assert len(query.filters) == 1
            assert query.filters[0].column == Column.Txn.CURRENCY
            assert query.filters[0].operator == ":"
            assert query.filters[0].value == "USD"

    def test_parse_limit_bare_last(self, temp_ctx: TempContext) -> None:
        """Test 'last N' (no time unit) is parsed as a result limit."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["last", "5"])
            assert query.limit == 5
            assert not query.filters

    def test_parse_limit_bare_first(self, temp_ctx: TempContext) -> None:
        """Test 'first N' is always parsed as a result limit (unambiguous)."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["first", "3"])
            assert query.limit == 3
            assert not query.filters

    def test_parse_limit_explicit(self, temp_ctx: TempContext) -> None:
        """Test explicit 'limit:N' filter syntax."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["limit:7"])
            assert query.limit == 7

    def test_parse_limit_explicit_overrides_natural_language(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """Test that explicit limit:N takes precedence over natural language."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["last", "5", "limit:10"])
            assert query.limit == 10

    def test_parse_last_n_unit_is_still_date_range(self, temp_ctx: TempContext) -> None:
        """Test 'last N <unit>' remains a date range, not a limit."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["last", "5", "days"])
            assert query.limit is None
            assert len(query.filters) == 2

    def test_parse_n_units_ago(self, temp_ctx: TempContext) -> None:
        """Test 'N units ago' produces a date range anchored on the number."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["3", "weeks", "ago"])
            assert len(query.filters) == 2
            assert query.filters[0].operator == ">="
            assert query.filters[1].operator == "<="
            today = datetime.now(TORONTO_TZ).date()
            expected_start = (today - timedelta(weeks=3)).strftime("%Y-%m-%d")
            assert query.filters[0].value == expected_start
            assert query.filters[1].value == today.strftime("%Y-%m-%d")

    def test_parse_this_year(self, temp_ctx: TempContext) -> None:
        """Test 'this year' produces a date range from Jan 1 to today."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["this", "year"])
            assert len(query.filters) == 2
            today = datetime.now(TORONTO_TZ).date()
            assert query.filters[0].value == f"{today.year}-01-01"
            assert query.filters[1].value == today.strftime("%Y-%m-%d")

    def test_parse_previous_is_alias_for_last(self, temp_ctx: TempContext) -> None:
        """Test 'previous month' behaves identically to 'last month'."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query_previous = parse_query_terms(["previous", "month"])
            query_last = parse_query_terms(["last", "month"])
            assert query_previous.filters == query_last.filters

    def test_parse_after_phrase(self, temp_ctx: TempContext) -> None:
        """Test 'after <date>' produces a single exclusive lower-bound filter."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["after", "2020"])
            assert len(query.filters) == 1
            assert query.filters[0].operator == ">"
            assert query.filters[0].value == "2020-12-31"

    def test_parse_before_phrase(self, temp_ctx: TempContext) -> None:
        """Test 'before <date>' produces a single exclusive upper-bound filter."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["before", "2030"])
            assert len(query.filters) == 1
            assert query.filters[0].operator == "<"
            assert query.filters[0].value == "2030-01-01"

    def test_parse_between_years(self, temp_ctx: TempContext) -> None:
        """Test 'between YEAR and YEAR' produces a full-year inclusive range."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["between", "2020", "and", "2021"])
            assert len(query.filters) == 2
            assert query.filters[0].operator == ">="
            assert query.filters[0].value == "2020-01-01"
            assert query.filters[1].operator == "<="
            assert query.filters[1].value == "2021-12-31"

    def test_parse_between_october_and_now_rolls_back_a_year(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """Test 'between X and now' rolls the start back if X hasn't happened yet."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["between", "october", "13", "and", "now"])
            assert len(query.filters) == 2
            assert query.filters[0].value <= query.filters[1].value

    def test_parse_from_to_months(self, temp_ctx: TempContext) -> None:
        """Test 'from MONTH to MONTH' resolves to full calendar months."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["from", "january", "to", "june"])
            assert len(query.filters) == 2
            assert query.filters[0].value.endswith("-01-01")
            assert query.filters[1].value.endswith("-06-30")

    def test_parse_from_without_to_is_open_ended(self, temp_ctx: TempContext) -> None:
        """Test 'from X' with no trailing 'to Y' ranges through today."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["from", "2025-01"])
            assert len(query.filters) == 2
            assert query.filters[0].operator == ">="
            assert query.filters[0].value == "2025-01-01"
            today = datetime.now(TORONTO_TZ).date()
            assert query.filters[1].value == today.strftime("%Y-%m-%d")

    def test_parse_since_month_year_phrase(self, temp_ctx: TempContext) -> None:
        """Test 'since MONTH YEAR' resolves via loose date parsing, not just digits."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["since", "july", "2023"])
            assert len(query.filters) == 2
            assert query.filters[0].operator == ">="
            assert query.filters[0].value == "2023-07-01"
            today = datetime.now(TORONTO_TZ).date()
            assert query.filters[1].value == today.strftime("%Y-%m-%d")

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

    def test_parse_unconfigured_column_filter_falls_back_to_text_search(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """Test that filters on non-existent (unconfigured) columns are not applied."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["Description~RECEIPTS"])
            assert not query.filters
            assert query.text_searches == ["Description~RECEIPTS"]

    def test_parse_column_name_keyword_is_not_treated_as_date(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """Test a bare column-name keyword (e.g. PRICE) isn't misread as a date term."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["PRICE"])
            assert not query.filters
            assert query.text_searches == ["PRICE"]

    def test_parse_single_word_exact_dates(self, temp_ctx: TempContext) -> None:
        """Test yesterday/today/tomorrow each resolve to a single-day date range."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            today = datetime.now(TORONTO_TZ).date()

            for term, offset in (("yesterday", -1), ("today", 0), ("tomorrow", 1)):
                query = parse_query_terms([term])
                expected = (today + timedelta(days=offset)).strftime("%Y-%m-%d")
                assert len(query.filters) == 2, term
                assert query.filters[0].value == expected, term
                assert query.filters[1].value == expected, term

    def test_parse_this_week_and_this_month(self, temp_ctx: TempContext) -> None:
        """Test 'this week'/'this month' resolve to period-start through today."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            today = datetime.now(TORONTO_TZ).date()

            query_week = parse_query_terms(["this", "week"])
            week_start = today - timedelta(days=today.weekday())
            assert len(query_week.filters) == 2
            assert query_week.filters[0].value == week_start.strftime("%Y-%m-%d")
            assert query_week.filters[1].value == today.strftime("%Y-%m-%d")

            query_month = parse_query_terms(["this", "month"])
            assert len(query_month.filters) == 2
            assert query_month.filters[0].value == today.replace(day=1).strftime(
                "%Y-%m-%d",
            )
            assert query_month.filters[1].value == today.strftime("%Y-%m-%d")

    def test_parse_last_day_and_last_week(self, temp_ctx: TempContext) -> None:
        """Test 'last day'/'last week' (no leading number) resolve correctly."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            today = datetime.now(TORONTO_TZ).date()

            query_day = parse_query_terms(["last", "day"])
            yesterday = today - timedelta(days=1)
            assert len(query_day.filters) == 2
            assert query_day.filters[0].value == yesterday.strftime("%Y-%m-%d")
            assert query_day.filters[1].value == yesterday.strftime("%Y-%m-%d")

            query_week = parse_query_terms(["last", "week"])
            this_week_start = today - timedelta(days=today.weekday())
            last_week_start = this_week_start - timedelta(weeks=1)
            last_week_end = last_week_start + timedelta(days=6)
            assert len(query_week.filters) == 2
            assert query_week.filters[0].value == last_week_start.strftime("%Y-%m-%d")
            assert query_week.filters[1].value == last_week_end.strftime("%Y-%m-%d")

    def test_parse_last_year(self, temp_ctx: TempContext) -> None:
        """Test 'last year' (no leading number) resolves to the full prior year."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            today = datetime.now(TORONTO_TZ).date()
            query = parse_query_terms(["last", "year"])
            assert len(query.filters) == 2
            assert query.filters[0].value == f"{today.year - 1}-01-01"
            assert query.filters[1].value == f"{today.year - 1}-12-31"

    def test_parse_last_month_wraps_to_previous_december(
        self,
        temp_ctx: TempContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test 'last month' rolls back into December of the prior year in January."""
        with temp_ctx() as _ctx:
            ensure_data_exists()

            class _FrozenDateTime(datetime):
                @classmethod
                def now(cls, tz: tzinfo | None = None) -> datetime:
                    return datetime(2025, 1, 15, tzinfo=tz)

            monkeypatch.setattr(query_parser, "datetime", _FrozenDateTime)

            query = parse_query_terms(["last", "month"])
            assert len(query.filters) == 2
            assert query.filters[0].value == "2024-12-01"
            assert query.filters[1].value == "2024-12-31"

    def test_parse_after_december_resolves_to_month_end(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """Test 'after december' resolves December's granularity to its last day."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["after", "december"])
            assert len(query.filters) == 1
            assert query.filters[0].operator == ">"
            assert query.filters[0].value.endswith("-12-31")

    @pytest.mark.parametrize(
        "terms",
        [
            pytest.param(["last", "zzzqux"], id="relative_phrase_no_match"),
            pytest.param(["first"], id="limit_phrase_missing_number"),
            pytest.param(["5", "zzzqux"], id="ago_phrase_no_match"),
            pytest.param(
                ["between", "zzzfoo", "zzzbar", "zzzbaz"],
                id="between_missing_and",
            ),
            pytest.param(
                ["between", "zzzfoo", "and", "zzzbar"],
                id="between_unresolvable_dates",
            ),
            pytest.param(["since", "zzzqux"], id="since_unresolvable"),
            pytest.param(["after", "zzzqux"], id="after_unresolvable"),
        ],
    )
    def test_parse_date_phrase_fallbacks_to_text_search(
        self,
        temp_ctx: TempContext,
        terms: list[str],
    ) -> None:
        """Test malformed/unrecognized date phrases fall back to text search."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(terms)
            assert not query.filters
            assert query.limit is None
            assert set(query.text_searches) == set(terms)

    def test_parse_limit_and_sort_invalid_falls_back_to_text_search(
        self,
        temp_ctx: TempContext,
    ) -> None:
        """Test bad limit:/sort: filters (bad value, unknown column) are ignored."""
        with temp_ctx() as _ctx:
            ensure_data_exists()
            query = parse_query_terms(["limit:abc", "sort:Bogus"])
            assert query.limit is None
            assert not query.sorts
            assert set(query.text_searches) == {"limit:abc", "sort:Bogus"}


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
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", DEFAULT_TICKERS[0]],
            )
            assert_cli_success(cli_result)
            # The results show the ticker was found
            assert_in_output(DEFAULT_TICKERS[0], cli_result)

    def test_query_action_keyword(self, temp_ctx: TempContext) -> None:
        """Test query with action keyword."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "BUY"],
            )
            assert_cli_success(cli_result)
            assert_in_output('Action="BUY"', cli_result)

    def test_query_date_range(self, temp_ctx: TempContext) -> None:
        """Test query with date range."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "2025-01-01:2025-12-31"],
            )
            assert_cli_success(cli_result)
            assert_in_output(">=2025-01-01", cli_result)
            assert_in_output("<=2025-12-31", cli_result)

    def test_query_with_sort(self, temp_ctx: TempContext) -> None:
        """Test query with sorting."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", DEFAULT_TICKERS[0], "sort:-Amount"],
            )
            assert_cli_success(cli_result)
            assert_in_output("sort:Amount(DESC)", cli_result)

    def test_query_combined_filters(self, temp_ctx: TempContext) -> None:
        """Test query with multiple combined filters."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "BUY", DEFAULT_TICKERS[0]],
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
                ticker = DEFAULT_TICKERS[0]
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
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "BUY", DEFAULT_TICKERS[0]],
            )
            assert_cli_success(cli_result)
            assert_in_output("BUY", cli_result)
            assert_in_output(DEFAULT_TICKERS[0], cli_result)

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

    def test_query_ticker_with_aliases(self, temp_ctx: TempContext) -> None:
        """Test that query respects ticker aliases."""
        with temp_ctx() as ctx:
            ensure_data_exists()

            # Create an alias
            old_ticker = DEFAULT_TICKERS[0]
            new_ticker = DEFAULT_TICKERS[1]

            run_cli_with_config(
                ctx.config,
                cli_app,
                ["tickers", "--add", old_ticker, new_ticker, "2025-01-01"],
            )

            # Query for the old ticker
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", old_ticker],
            )
            assert_cli_success(cli_result)
            # Should find results because it searches for ticker family

    def test_query_command_with_aliases(self, temp_ctx: TempContext) -> None:
        """Test that the query command correctly uses ticker aliases.

        This comprehensive test validates that:
        1. Aliasing a ticker makes both old and new tickers searchable
        2. Querying the old ticker finds both old and new ticker transactions
        3. Querying the new ticker finds both old and new ticker transactions
        4. Querying an unrelated ticker finds only its own transactions
        """
        with temp_ctx() as ctx:
            config = ctx.config
            # 1. Setup: Ensure mock data exists
            ensure_data_exists()
            # Verify that transactions for DEFAULT_TICKERS are created
            total_mock_txns = DEFAULT_TXN_COUNT * len(DEFAULT_TICKERS)
            with get_connection() as conn:
                assert get_row_count(conn, Table.TXNS) == total_mock_txns

            # Choose two tickers for aliasing and one unrelated
            ticker_alias_old = DEFAULT_TICKERS[0]  # e.g., "SPY"
            ticker_alias_new = DEFAULT_TICKERS[1]  # e.g., "AAPL"
            ticker_unrelated = DEFAULT_TICKERS[2]  # e.g., "O"
            alias_effective_date = "2025-01-01"  # Before any mock transactions

            # 2. Create an alias: ticker_alias_old -> ticker_alias_new
            run_cli_with_config(
                config,
                cli_app,
                [
                    "tickers",
                    "--add",
                    ticker_alias_old,
                    ticker_alias_new,
                    alias_effective_date,
                ],
            )

            # 3. Query for the old ticker ticker_alias_old
            result_a = run_cli_with_config(config, cli_app, ["query", ticker_alias_old])
            assert_cli_success(result_a)
            # Should find transactions for ticker_alias_old and ticker_alias_new
            expected_txns_count_aliased = (
                DEFAULT_TXN_COUNT - _expected_excluded_txns_per_ticker
            ) * 2
            assert_in_output(
                f"Found {expected_txns_count_aliased} matching transaction(s).",
                result_a,
            )
            assert_in_output(f" {ticker_alias_old} ", result_a)
            assert_in_output(f" {ticker_alias_new} ", result_a)
            assert_not_in_output(f" {ticker_unrelated} ", result_a)

            # 4. Query for the new ticker ticker_alias_new
            result_b = run_cli_with_config(config, cli_app, ["query", ticker_alias_new])
            assert_cli_success(result_b)
            # Should also find transactions for ticker_alias_old and ticker_alias_new
            assert_in_output(
                f"Found {expected_txns_count_aliased} matching transaction(s).",
                result_b,
            )
            assert_in_output(f" {ticker_alias_old} ", result_b)
            assert_in_output(f" {ticker_alias_new} ", result_b)
            assert_not_in_output(f" {ticker_unrelated} ", result_b)

            # 5. Query for the unrelated ticker ticker_unrelated
            result_c = run_cli_with_config(config, cli_app, ["query", ticker_unrelated])
            assert_cli_success(result_c)
            # Should find only transactions for ticker_unrelated
            expected_txns_count_single = (
                DEFAULT_TXN_COUNT - _expected_excluded_txns_per_ticker
            )
            assert_in_output(
                f"Found {expected_txns_count_single} matching transaction(s).",
                result_c,
            )
            assert_in_output(f" {ticker_unrelated} ", result_c)
            assert_not_in_output(f" {ticker_alias_old} ", result_c)
            assert_not_in_output(f" {ticker_alias_new} ", result_c)

    def test_query_action_with_date_phrase(self, temp_ctx: TempContext) -> None:
        """Test that action keywords work after date phrases.

        Validates that when parsing "BUY last month", the action is correctly
        captured and not lost to the date parser.
        """
        with temp_ctx() as ctx:
            ensure_data_exists()
            # Query for BUY action with a date range
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "BUY", "last", "month"],
            )
            assert_cli_success(cli_result)
            # Verify BUY action is in the filters
            assert_in_output('Action="BUY"', cli_result)

    def test_query_multiple_date_patterns(self, temp_ctx: TempContext) -> None:
        """Test various natural language date patterns."""
        with temp_ctx() as ctx:
            ensure_data_exists()

            # Test "last N days"
            result_days = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "last", "7", "days"],
            )
            assert_cli_success(result_days)
            assert_in_output("Filters:", result_days)

            # Test "last N weeks"
            result_weeks = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "last", "3", "weeks"],
            )
            assert_cli_success(result_weeks)
            assert_in_output("Filters:", result_weeks)

            # Test "since YEAR"
            result_since = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "since", "2024"],
            )
            assert_cli_success(result_since)
            assert_in_output("Filters:", result_since)

    def test_query_complex_combination(self, temp_ctx: TempContext) -> None:
        """Test complex query with action, ticker, date, and sort."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "BUY", "AAPL", "last", "2", "years", "sort:-Amount"],
            )
            assert_cli_success(cli_result)
            # Verify all components are parsed
            assert_in_output('Action="BUY"', cli_result)
            assert_in_output('Ticker="AAPL"', cli_result)
            assert_in_output("sort:Amount", cli_result)

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
            result_ticker = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", DEFAULT_TICKERS[0]],
            )
            assert_cli_success(result_ticker)
            assert_in_output(f'Ticker="{DEFAULT_TICKERS[0]}"', result_ticker)

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
            assert_in_output("limit:5", cli_result)
            assert_in_output("Found 5 matching transaction(s).", cli_result)

    def test_query_limit_explicit(self, temp_ctx: TempContext) -> None:
        """Test explicit 'limit:N' filter syntax."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            cli_result = run_cli_with_config(ctx.config, cli_app, ["query", "limit:3"])
            assert_cli_success(cli_result)
            assert_in_output("limit:3", cli_result)
            assert_in_output("Found 3 matching transaction(s).", cli_result)

    def test_query_currency_keyword(self, temp_ctx: TempContext) -> None:
        """Test query with a bare currency keyword."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            cli_result = run_cli_with_config(ctx.config, cli_app, ["query", "USD"])
            assert_cli_success(cli_result)
            assert_in_output('$="USD"', cli_result)

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
            expected_total = DEFAULT_TXN_COUNT * len(DEFAULT_TICKERS)
            output_msg = f"Found {expected_total} matching transaction(s)"
            assert_in_output(output_msg, cli_result)

    def test_query_after_before_bounds(self, temp_ctx: TempContext) -> None:
        """Test single-sided 'after'/'before' date phrases."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            expected_total = DEFAULT_TXN_COUNT * len(DEFAULT_TICKERS)

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

    def test_query_since_month_year_with_sort(self, temp_ctx: TempContext) -> None:
        """Test the documented 'since MONTH YEAR ... sort ... last N' combo."""
        with temp_ctx() as ctx:
            ensure_data_exists()
            cli_result = run_cli_with_config(
                ctx.config,
                cli_app,
                ["query", "since", "july", "2023", "sort:amount", "last", "3"],
            )
            assert_cli_success(cli_result)
            assert_in_output("TxnDate>=2023-07-01", cli_result)
            assert_in_output("limit:3", cli_result)
