"""Tests for the smart query parser.

These exercise pure parsing logic: term -> ParsedQuery(filters/sorts/limit/text).
`parse_query_terms` consults the database only for reference lookups (distinct
tickers, distinct accounts, live Txns columns), so those are stubbed with fixed
values here. That keeps these tests about parsing rather than about data setup,
and avoids provisioning a sqlite db + parquet files for every case. The real
lookup path stays covered by the CLI query tests in test_query.py, which run
against genuine mock data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from cli import query_parser
from cli.query_parser import parse_query_terms
from utils import TORONTO_TZ
from utils.constants import DEFAULT_TICKERS, Column

if TYPE_CHECKING:
    from collections.abc import Generator

# Mirrors what the mock data generator produces, so parsed values stay
# representative of real folio contents.
MOCK_TICKERS = set(DEFAULT_TICKERS)
MOCK_ACCOUNTS = {"MOCK-ACCOUNT", "TFSA", "RRSP"}
MOCK_COLUMNS = [column.value for column in Column.Txn]


@pytest.fixture(autouse=True)
def stub_db_lookups() -> Generator[None, Any]:
    """Serve the parser's reference lookups from fixed values instead of sqlite."""
    with patch.object(
        query_parser,
        "_get_db_lookup_values",
        return_value=(MOCK_TICKERS, MOCK_ACCOUNTS, MOCK_COLUMNS),
    ):
        yield


class TestQueryParser:
    """Tests for the query parser logic."""

    def test_parse_account(self) -> None:
        """A known account name is recognized as an Account filter."""
        account = "MOCK-ACCOUNT"
        query = parse_query_terms([account])
        account_filters = [f for f in query.filters if f.column == Column.Txn.ACCOUNT]
        assert len(account_filters) == 1
        assert account_filters[0].value == account

    def test_parse_ticker(self) -> None:
        """Test parsing a simple ticker."""
        query = parse_query_terms([DEFAULT_TICKERS[0]])
        assert len(query.filters) == 1
        assert query.filters[0].column == Column.Txn.TICKER
        assert query.filters[0].operator == ":"
        assert query.filters[0].value == DEFAULT_TICKERS[0]

    def test_parse_action(self) -> None:
        """Test parsing an action keyword."""
        query = parse_query_terms(["BUY"])
        assert len(query.filters) == 1
        assert query.filters[0].column == Column.Txn.ACTION
        assert query.filters[0].operator == ":"
        assert query.filters[0].value == "BUY"

    def test_parse_exact_date(self) -> None:
        """Test parsing an exact date."""
        query = parse_query_terms(["2025-01-15"])
        assert len(query.filters) == 1
        assert query.filters[0].column == Column.Txn.TXN_DATE
        assert query.filters[0].operator == ":"
        assert query.filters[0].value == "2025-01-15"

    def test_parse_month_day_year_phrase(self) -> None:
        """Test parsing 'MONTH DAY YEAR' (e.g. 'august 10 2024') as an exact date."""
        query = parse_query_terms(["august", "10", "2024"])
        assert len(query.filters) == 1
        assert query.filters[0].column == Column.Txn.TXN_DATE
        assert query.filters[0].operator == ":"
        assert query.filters[0].value == "2024-08-10"

    def test_parse_bare_month_name_is_not_a_date(self) -> None:
        """Test a bare month name with no day stays a text search (e.g. ticker MAY)."""
        query = parse_query_terms(["MAY"])
        assert not query.filters
        assert query.text_searches == ["MAY"]

    def test_parse_date_range(self) -> None:
        """Test parsing a date range (from:to)."""
        query = parse_query_terms(["2025-01-01:2025-12-31"])
        # Should have two filters for date range
        assert len(query.filters) == 2
        assert query.filters[0].column == Column.Txn.TXN_DATE
        assert query.filters[0].operator == ">="
        assert query.filters[1].column == Column.Txn.TXN_DATE
        assert query.filters[1].operator == "<="

    def test_parse_date_comparison(self) -> None:
        """Test parsing date comparison operators."""
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

    def test_parse_explicit_filter_exact(self) -> None:
        """Test parsing explicit filter with exact match operator."""
        query = parse_query_terms(["Action:BUY"])
        assert len(query.filters) == 1
        assert query.filters[0].column == Column.Txn.ACTION
        assert query.filters[0].operator == ":"
        assert query.filters[0].value == "BUY"

    def test_parse_explicit_filter_contains(self) -> None:
        """Test parsing explicit filter with contains operator."""
        query = parse_query_terms(["Account~TFSA"])
        assert len(query.filters) == 1
        assert query.filters[0].column == Column.Txn.ACCOUNT
        assert query.filters[0].operator == "~"
        assert query.filters[0].value == "TFSA"

    def test_parse_explicit_filter_comparison(self) -> None:
        """Test parsing explicit filter with comparison operators."""
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

    def test_parse_sort_ascending(self) -> None:
        """Test parsing sort in ascending order."""
        query = parse_query_terms(["sort:Ticker"])
        assert len(query.sorts) == 1
        assert query.sorts[0].column == Column.Txn.TICKER
        assert query.sorts[0].direction == "asc"

    def test_parse_sort_descending(self) -> None:
        """Test parsing sort in descending order."""
        query = parse_query_terms(["sort:-Amount"])
        assert len(query.sorts) == 1
        assert query.sorts[0].column == Column.Txn.AMOUNT
        assert query.sorts[0].direction == "desc"

    def test_parse_multiple_sorts(self) -> None:
        """Test parsing multiple sort specifications."""
        query = parse_query_terms(["sort:Ticker", "sort:-TxnDate"])
        assert len(query.sorts) == 2
        assert query.sorts[0].column == Column.Txn.TICKER
        assert query.sorts[0].direction == "asc"
        assert query.sorts[1].column == Column.Txn.TXN_DATE
        assert query.sorts[1].direction == "desc"

    def test_parse_text_search(self) -> None:
        """Test parsing unrecognized terms as text searches."""
        query = parse_query_terms(["UNKNOWN_TERM"])
        assert len(query.text_searches) == 1
        assert query.text_searches[0] == "UNKNOWN_TERM"

    def test_parse_combined_query(self) -> None:
        """Test parsing a complex combined query."""
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

    def test_parse_currency_keyword(self) -> None:
        """Test parsing a bare currency keyword (exact match, like Action)."""
        query = parse_query_terms(["USD"])
        assert len(query.filters) == 1
        assert query.filters[0].column == Column.Txn.CURRENCY
        assert query.filters[0].operator == ":"
        assert query.filters[0].value == "USD"

    def test_parse_limit_bare_last(self) -> None:
        """Test 'last N' (no time unit) is parsed as a result limit."""
        query = parse_query_terms(["last", "5"])
        assert query.limit == 5
        assert not query.filters

    def test_parse_limit_bare_first(self) -> None:
        """Test 'first N' is always parsed as a result limit (unambiguous)."""
        query = parse_query_terms(["first", "3"])
        assert query.limit == 3
        assert not query.filters

    def test_parse_limit_explicit(self) -> None:
        """Test explicit 'limit:N' filter syntax."""
        query = parse_query_terms(["limit:7"])
        assert query.limit == 7

    def test_parse_limit_explicit_overrides_natural_language(self) -> None:
        """Test that explicit limit:N takes precedence over natural language."""
        query = parse_query_terms(["last", "5", "limit:10"])
        assert query.limit == 10

    def test_parse_last_n_unit_is_still_date_range(self) -> None:
        """Test 'last N <unit>' remains a date range, not a limit."""
        query = parse_query_terms(["last", "5", "days"])
        assert query.limit is None
        assert len(query.filters) == 2

    def test_parse_n_units_ago(self) -> None:
        """Test 'N units ago' produces a date range anchored on the number."""
        query = parse_query_terms(["3", "weeks", "ago"])
        assert len(query.filters) == 2
        assert query.filters[0].operator == ">="
        assert query.filters[1].operator == "<="
        today = datetime.now(TORONTO_TZ).date()
        expected_start = (today - timedelta(weeks=3)).strftime("%Y-%m-%d")
        assert query.filters[0].value == expected_start
        assert query.filters[1].value == today.strftime("%Y-%m-%d")

    def test_parse_this_year(self) -> None:
        """Test 'this year' produces a date range from Jan 1 to today."""
        query = parse_query_terms(["this", "year"])
        assert len(query.filters) == 2
        today = datetime.now(TORONTO_TZ).date()
        assert query.filters[0].value == f"{today.year}-01-01"
        assert query.filters[1].value == today.strftime("%Y-%m-%d")

    def test_parse_previous_is_alias_for_last(self) -> None:
        """Test 'previous month' behaves identically to 'last month'."""
        query_previous = parse_query_terms(["previous", "month"])
        query_last = parse_query_terms(["last", "month"])
        assert query_previous.filters == query_last.filters

    def test_parse_after_phrase(self) -> None:
        """Test 'after <date>' produces a single exclusive lower-bound filter."""
        query = parse_query_terms(["after", "2020"])
        assert len(query.filters) == 1
        assert query.filters[0].operator == ">"
        assert query.filters[0].value == "2020-12-31"

    def test_parse_before_phrase(self) -> None:
        """Test 'before <date>' produces a single exclusive upper-bound filter."""
        query = parse_query_terms(["before", "2030"])
        assert len(query.filters) == 1
        assert query.filters[0].operator == "<"
        assert query.filters[0].value == "2030-01-01"

    def test_parse_between_years(self) -> None:
        """Test 'between YEAR and YEAR' produces a full-year inclusive range."""
        query = parse_query_terms(["between", "2020", "and", "2021"])
        assert len(query.filters) == 2
        assert query.filters[0].operator == ">="
        assert query.filters[0].value == "2020-01-01"
        assert query.filters[1].operator == "<="
        assert query.filters[1].value == "2021-12-31"

    def test_parse_between_october_and_now_rolls_back_a_year(self) -> None:
        """Test 'between X and now' rolls the start back if X hasn't happened yet."""
        query = parse_query_terms(["between", "october", "13", "and", "now"])
        assert len(query.filters) == 2
        assert query.filters[0].value <= query.filters[1].value

    def test_parse_from_to_months(self) -> None:
        """Test 'from MONTH to MONTH' resolves to full calendar months."""
        query = parse_query_terms(["from", "january", "to", "june"])
        assert len(query.filters) == 2
        assert query.filters[0].value.endswith("-01-01")
        assert query.filters[1].value.endswith("-06-30")

    def test_parse_from_without_to_is_open_ended(self) -> None:
        """Test 'from X' with no trailing 'to Y' ranges through today."""
        query = parse_query_terms(["from", "2025-01"])
        assert len(query.filters) == 2
        assert query.filters[0].operator == ">="
        assert query.filters[0].value == "2025-01-01"
        today = datetime.now(TORONTO_TZ).date()
        assert query.filters[1].value == today.strftime("%Y-%m-%d")

    def test_parse_since_month_year_phrase(self) -> None:
        """Test 'since MONTH YEAR' resolves via loose date parsing, not just digits."""
        query = parse_query_terms(["since", "july", "2023"])
        assert len(query.filters) == 2
        assert query.filters[0].operator == ">="
        assert query.filters[0].value == "2023-07-01"
        today = datetime.now(TORONTO_TZ).date()
        assert query.filters[1].value == today.strftime("%Y-%m-%d")

    def test_parse_unconfigured_column_filter_falls_back_to_text_search(self) -> None:
        """Test that filters on non-existent (unconfigured) columns are not applied."""
        query = parse_query_terms(["Description~RECEIPTS"])
        assert not query.filters
        assert query.text_searches == ["Description~RECEIPTS"]

    def test_parse_column_name_keyword_is_not_treated_as_date(self) -> None:
        """Test a bare column-name keyword (e.g. PRICE) isn't misread as a date term."""
        query = parse_query_terms(["PRICE"])
        assert not query.filters
        assert query.text_searches == ["PRICE"]

    def test_parse_single_word_exact_dates(self) -> None:
        """Test yesterday/today/tomorrow each resolve to a single-day date range."""
        today = datetime.now(TORONTO_TZ).date()

        for term, offset in (("yesterday", -1), ("today", 0), ("tomorrow", 1)):
            query = parse_query_terms([term])
            expected = (today + timedelta(days=offset)).strftime("%Y-%m-%d")
            assert len(query.filters) == 2, term
            assert query.filters[0].value == expected, term
            assert query.filters[1].value == expected, term

    def test_parse_this_week_and_this_month(self) -> None:
        """Test 'this week'/'this month' resolve to period-start through today."""
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

    def test_parse_last_day_and_last_week(self) -> None:
        """Test 'last day'/'last week' (no leading number) resolve correctly."""
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

    def test_parse_last_year(self) -> None:
        """Test 'last year' (no leading number) resolves to the full prior year."""
        today = datetime.now(TORONTO_TZ).date()
        query = parse_query_terms(["last", "year"])
        assert len(query.filters) == 2
        assert query.filters[0].value == f"{today.year - 1}-01-01"
        assert query.filters[1].value == f"{today.year - 1}-12-31"

    def test_parse_last_month_wraps_to_previous_december(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test 'last month' rolls back into December of the prior year in January."""

        class _FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz: tzinfo | None = None) -> datetime:
                return datetime(2025, 1, 15, tzinfo=tz)

        monkeypatch.setattr(query_parser, "datetime", _FrozenDateTime)

        query = parse_query_terms(["last", "month"])
        assert len(query.filters) == 2
        assert query.filters[0].value == "2024-12-01"
        assert query.filters[1].value == "2024-12-31"

    def test_parse_after_december_resolves_to_month_end(self) -> None:
        """Test 'after december' resolves December's granularity to its last day."""
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
        terms: list[str],
    ) -> None:
        """Test malformed/unrecognized date phrases fall back to text search."""
        query = parse_query_terms(terms)
        assert not query.filters
        assert query.limit is None
        assert set(query.text_searches) == set(terms)

    def test_parse_limit_and_sort_invalid_falls_back_to_text_search(self) -> None:
        """Test bad limit:/sort: filters (bad value, unknown column) are ignored."""
        query = parse_query_terms(["limit:abc", "sort:Bogus"])
        assert query.limit is None
        assert not query.sorts
        assert set(query.text_searches) == {"limit:abc", "sort:Bogus"}
