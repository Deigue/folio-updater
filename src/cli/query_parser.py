"""Smart query parser for the folio CLI."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from dateparser.date import DateDataParser

from db import get_columns, get_connection, get_distinct_set
from utils import TORONTO_TZ
from utils.constants import Action, Column, Currency, Table

if TYPE_CHECKING:
    from collections.abc import Sequence

# Constants for date parsing
_DECEMBER_MONTH = 12
_TIME_UNITS = {"day", "week", "month", "year"}
_MAX_LOOSE_DATE_WORDS = 3
_MONTH_NAMES = {
    "january", "jan",
    "february", "feb",
    "march", "mar",
    "april", "apr",
    "may",
    "june", "jun",
    "july", "jul",
    "august", "aug",
    "september", "sep", "sept",
    "october", "oct",
    "november", "nov",
    "december", "dec",
}  # fmt: skip


@dataclass
class QueryFilter:
    """Represents a single filter in a query."""

    column: str
    operator: str  # ":", "~", ">", "<", ">=", "<="
    value: str

    def __repr__(self) -> str:
        """Return string representation of the filter."""
        if self.operator == ":":
            return f'{self.column}="{self.value}"'
        return f"{self.column}{self.operator}{self.value}"


@dataclass
class QuerySort:
    """Represents a sort specification in a query."""

    column: str
    direction: str  # "asc" or "desc"

    def __repr__(self) -> str:
        """Return string representation of the sort."""
        direction_str = "DESC" if self.direction == "desc" else "ASC"
        return f"sort:{self.column}({direction_str})"


@dataclass
class ParsedQuery:
    """Represents a fully parsed query."""

    filters: list[QueryFilter] = field(default_factory=list)
    text_searches: list[str] = field(default_factory=list)  # General text search terms
    sorts: list[QuerySort] = field(default_factory=list)
    limit: int | None = None

    def __repr__(self) -> str:
        """Return string representation of the parsed query."""
        query: list[str] = [repr(f) for f in self.filters]
        query.extend(f'text~"{search}"' for search in self.text_searches)
        query.extend(repr(s) for s in self.sorts)
        if self.limit is not None:
            query.append(f"limit:{self.limit}")
        if not query:
            return "Querying all transactions (no filters applied)."
        return "Filters: " + ", ".join(query)


def _get_db_lookup_values() -> tuple[set[str], set[str], list[str]]:
    """Fetch distinct tickers, accounts, and live Txns columns from the database."""
    with get_connection() as conn:
        db_tickers = get_distinct_set(conn, Table.TXNS, Column.Txn.TICKER)
        db_accounts = get_distinct_set(conn, Table.TXNS, Column.Txn.ACCOUNT)
        live_columns = get_columns(conn, Table.TXNS)
    return db_tickers, db_accounts, live_columns


def parse_query_terms(terms: Sequence[str]) -> ParsedQuery:
    """Parse a list of query terms into a structured query.

    Multi-stage parsing process:
    1. Natural language dates (e.g., "last 7 months", "since 2023", "yesterday")
    2. Explicit filters, sorts, action keywords, tickers, accounts
    3. Remaining terms as text searches

    Args:
        terms: A sequence of strings from the CLI.

    Returns:
        A ParsedQuery object with parsed filters, text searches, and sorts.
    """
    query = ParsedQuery()
    remaining_terms: list[str] = []
    action_values = {action.value for action in Action}
    currency_values = {currency.value for currency in Currency}
    db_tickers, db_accounts, live_columns = _get_db_lookup_values()
    valid_columns = get_valid_column_names(live_columns)

    # Stage 1: Try to parse natural language dates from all terms
    terms_list = list(terms)
    i = 0
    while i < len(terms_list):
        term = terms_list[i]

        # Try to parse date phrase (handles multi-word phrases)
        date_result = _try_parse_natural_language_date(
            terms_list,
            i,
            query,
        )
        if date_result is not None:
            # date_result tells us how many terms were consumed
            i += date_result
            continue

        remaining_terms.append(term)
        i += 1

    # Stage 2: Parse remaining terms for explicit filters, sorts, keywords
    for term in remaining_terms:
        if _process_term_for_filters(
            term,
            query,
            valid_columns,
            action_values,
            currency_values,
            db_tickers,
            db_accounts,
        ):
            continue

        # Stage 3: Default: treat as text search
        query.text_searches.append(term)

    return query


def _process_term_for_filters(  # noqa: PLR0917
    term: str,
    query: ParsedQuery,
    valid_columns: dict[str, str],
    action_values: set[str],
    currency_values: set[str],
    db_tickers: set[str],
    db_accounts: set[str],
) -> bool:
    """Process a single term, checking for explicit filters, sorts, keywords, etc."""
    processed = False
    # Try explicit filter syntax (column:value, column~value, etc.)
    if (
        _try_parse_limit(term, query)
        or _try_parse_explicit_filter(term, query, valid_columns)
        or _try_parse_sort(term, query, valid_columns)
        or _try_parse_date_filter(term, query)
    ):
        processed = True
    else:
        # Check for KNOWN keywords
        term_upper = term.upper()
        if term_upper in action_values:
            query.filters.append(QueryFilter(Column.Txn.ACTION, ":", term_upper))
            processed = True
        elif term_upper in currency_values:
            query.filters.append(QueryFilter(Column.Txn.CURRENCY, ":", term_upper))
            processed = True
        elif term_upper in db_tickers:
            query.filters.append(QueryFilter(Column.Txn.TICKER, ":", term_upper))
            processed = True
        elif term in db_accounts:
            query.filters.append(QueryFilter(Column.Txn.ACCOUNT, ":", term))
            processed = True
    return processed


def _try_parse_natural_language_date(
    terms: list[str],
    start_index: int,
    query: ParsedQuery,
) -> int | None:
    """Try to parse natural language date phrases.

    Args:
        terms: List of all terms
        start_index: Index of current term to start parsing from
        query: Query to add filters to
        db_tickers: Set of known tickers (to avoid false matches)
        db_accounts: Set of known accounts (to avoid false matches)

    Returns:
        Number of terms consumed if successful, None otherwise.
    """
    term = terms[start_index]
    term_upper = term.upper()

    # Quick skip false positives
    if term_upper in {
        "SORT",
        Column.Txn.ACTION.name,
        Column.Txn.AMOUNT.name,
        Column.Txn.PRICE.name,
        Column.Txn.UNITS.name,
        Column.Txn.ACCOUNT.name,
        Column.Txn.TICKER.name,
    }:
        return None

    # Check for single-word relative dates
    if term_upper in {"TODAY", "TOMORROW", "YESTERDAY"}:
        return _apply_exact_date_phrase(term.lower(), query)

    # "N days/weeks/months/years ago" -> anchored on the leading number, since
    # "ago" itself is a trailing word and can't trigger a forward scan.
    if term.isdigit():
        return _try_parse_ago_phrase(terms, start_index, query)

    # Fast-path phrase grammars with their own explicit trigger words.
    for fast_path in (
        _try_parse_limit_phrase,
        _try_parse_range_phrase,
        _try_parse_bound_phrase,
        _try_parse_since_phrase,
        _try_parse_month_day_phrase,
    ):
        consumed = fast_path(terms, start_index, query)
        if consumed is not None:
            return consumed

    # Only try multi-word phrases for relative date keywords
    relative_keywords = {"LAST", "PREVIOUS", "THIS"}
    if term_upper not in relative_keywords:
        # Single word that's not a relative keyword - don't try to parse as date
        return None

    return _try_parse_relative_phrase(terms, start_index, query)


def _apply_exact_date_phrase(phrase_lower: str, query: ParsedQuery) -> int:
    """Apply a single-word exact date phrase (today/tomorrow/yesterday)."""
    start_date, end_date = _parse_relative_date_range(phrase_lower)
    if start_date:
        query.filters.append(QueryFilter(Column.Txn.TXN_DATE, ">=", start_date))
    if end_date:
        query.filters.append(QueryFilter(Column.Txn.TXN_DATE, "<=", end_date))
    return 1


def _try_parse_relative_phrase(
    terms: list[str],
    start_index: int,
    query: ParsedQuery,
) -> int | None:
    """Try LAST/PREVIOUS/THIS multi-word relative date phrases.

    Builds phrases from shortest to longest, trying only the minimum number
    of words needed to form a recognized relative date phrase.
    """
    for end_idx in range(start_index + 1, min(start_index + 5, len(terms) + 1)):
        phrase = " ".join(terms[start_index:end_idx])
        num_words = end_idx - start_index

        # Only process if it looks like a relative date phrase
        if _is_relative_date(phrase):
            # Parse the phrase to extract the date range
            start_date, end_date = _parse_relative_date_range(phrase)
            if start_date and end_date:
                query.filters.append(
                    QueryFilter(Column.Txn.TXN_DATE, ">=", start_date),
                )
                query.filters.append(
                    QueryFilter(Column.Txn.TXN_DATE, "<=", end_date),
                )
                return num_words

    return None


def _try_parse_limit_phrase(
    terms: list[str],
    start_index: int,
    query: ParsedQuery,
) -> int | None:
    """Try to parse 'first N' / 'last N' (no time unit) as a result limit.

    'first' has no date meaning, so 'first N' is always a limit. 'last N' is a
    limit only when no time unit follows (`last 5 days` is a date range).

    Returns:
        2 (terms consumed) if matched, otherwise None.
    """
    term_upper = terms[start_index].upper()
    if term_upper not in {"FIRST", "LAST"}:
        return None
    if start_index + 1 >= len(terms):
        return None

    next_term = terms[start_index + 1]
    if not next_term.isdigit():
        return None

    unit_follows = (
        start_index + 2 < len(terms)
        and terms[start_index + 2].lower().rstrip("s") in _TIME_UNITS
    )
    if term_upper == "LAST" and unit_follows:
        return None

    query.limit = int(next_term)
    return 2


def _try_parse_ago_phrase(
    terms: list[str],
    start_index: int,
    query: ParsedQuery,
) -> int | None:
    """Try to parse 'N days/weeks/months/years ago' anchored on the number."""
    max_end = min(start_index + 4, len(terms) + 1)
    for end_idx in range(start_index + 2, max_end):
        phrase = " ".join(terms[start_index:end_idx])
        start_date, end_date = _parse_ago_pattern(phrase.lower())
        if start_date and end_date:
            query.filters.append(QueryFilter(Column.Txn.TXN_DATE, ">=", start_date))
            query.filters.append(QueryFilter(Column.Txn.TXN_DATE, "<=", end_date))
            return end_idx - start_index
    return None


def _try_parse_range_phrase(
    terms: list[str],
    start_index: int,
    query: ParsedQuery,
) -> int | None:
    """Try to parse 'between X and Y' / 'from X to Y' date range phrases.

    'from X' with no trailing 'to Y' is treated as an open-ended range from X
    through today (like 'since X').
    """
    term_upper = terms[start_index].upper()
    if term_upper == "BETWEEN":
        delimiter = "and"
    elif term_upper == "FROM":
        delimiter = "to"
    else:
        return None

    max_start_words = 2
    delim_search_end = min(start_index + 2 + max_start_words, len(terms))
    delim_index: int | None = None
    for idx in range(start_index + 2, delim_search_end):
        if terms[idx].lower() == delimiter:
            delim_index = idx
            break
    if delim_index is None:
        if term_upper == "FROM":
            return _try_parse_open_start_range(
                terms,
                start_index,
                query,
                prefer="current_period",
            )
        return None

    start_phrase = " ".join(terms[start_index + 1 : delim_index])
    max_end_words = min(_MAX_LOOSE_DATE_WORDS, len(terms) - delim_index - 1)

    for end_words in range(max_end_words, 0, -1):
        end_phrase = " ".join(terms[delim_index + 1 : delim_index + 1 + end_words])
        start_date = _resolve_loose_date(start_phrase, boundary="start")
        end_date = _resolve_loose_date(end_phrase, boundary="end")
        if start_date is None or end_date is None:
            continue

        if start_date > end_date:
            # e.g. "between october 13 and now": October hasn't happened yet
            # this year, so the range must reach back to last year's October.
            start_date = start_date.replace(year=start_date.year - 1)

        query.filters.append(
            QueryFilter(Column.Txn.TXN_DATE, ">=", start_date.strftime("%Y-%m-%d")),
        )
        query.filters.append(
            QueryFilter(Column.Txn.TXN_DATE, "<=", end_date.strftime("%Y-%m-%d")),
        )
        return (delim_index + 1 + end_words) - start_index

    return None


def _try_parse_open_start_range(
    terms: list[str],
    start_index: int,
    query: ParsedQuery,
    *,
    prefer: str,
) -> int | None:
    """Try to resolve 'TRIGGER X' as an open-ended range from X through today."""
    max_words = min(_MAX_LOOSE_DATE_WORDS, len(terms) - start_index - 1)
    for num_words in range(max_words, 0, -1):
        phrase = " ".join(terms[start_index + 1 : start_index + 1 + num_words])
        start_date = _resolve_loose_date(phrase, boundary="start", prefer=prefer)
        if start_date is not None:
            today = datetime.now(TORONTO_TZ).date()
            query.filters.append(
                QueryFilter(Column.Txn.TXN_DATE, ">=", start_date.strftime("%Y-%m-%d")),
            )
            query.filters.append(
                QueryFilter(Column.Txn.TXN_DATE, "<=", today.strftime("%Y-%m-%d")),
            )
            return num_words + 1

    return None


def _try_parse_since_phrase(
    terms: list[str],
    start_index: int,
    query: ParsedQuery,
) -> int | None:
    """Try to parse 'since X' as an open-ended range from X through today."""
    if terms[start_index].upper() != "SINCE":
        return None
    return _try_parse_open_start_range(terms, start_index, query, prefer="past")


def _try_parse_bound_phrase(
    terms: list[str],
    start_index: int,
    query: ParsedQuery,
) -> int | None:
    """Try to parse 'after X' / 'before X' single-sided date phrases."""
    term_upper = terms[start_index].upper()
    if term_upper not in {"AFTER", "BEFORE"}:
        return None

    max_words = min(_MAX_LOOSE_DATE_WORDS, len(terms) - start_index - 1)
    boundary = "end" if term_upper == "AFTER" else "start"
    operator = ">" if term_upper == "AFTER" else "<"

    for num_words in range(max_words, 0, -1):
        phrase = " ".join(terms[start_index + 1 : start_index + 1 + num_words])
        resolved = _resolve_loose_date(phrase, boundary=boundary, prefer="past")
        if resolved is not None:
            resolved_str = resolved.strftime("%Y-%m-%d")
            query.filters.append(
                QueryFilter(Column.Txn.TXN_DATE, operator, resolved_str),
            )
            return num_words + 1

    return None


def _try_parse_month_day_phrase(
    terms: list[str],
    start_index: int,
    query: ParsedQuery,
) -> int | None:
    """Try to parse an exact day given as 'MONTH DAY [YEAR]' (e.g. 'august 10 2024').

    Only matches when a specific day is actually present in the phrase (i.e.
    dateparser resolves it at "day" granularity) - a bare month name like
    "august" alone stays a possible ticker/text-search term.
    """
    if terms[start_index].lower() not in _MONTH_NAMES:
        return None

    max_words = min(_MAX_LOOSE_DATE_WORDS, len(terms) - start_index)
    for num_words in range(max_words, 0, -1):
        phrase = " ".join(terms[start_index : start_index + num_words])
        result = _get_loose_date_data(phrase)
        if result is not None and result[1] == "day":
            resolved_date, _period = result
            resolved_str = resolved_date.strftime("%Y-%m-%d")
            query.filters.append(
                QueryFilter(Column.Txn.TXN_DATE, ":", resolved_str),
            )
            return num_words

    return None


def _last_day_of_month(year: int, month: int) -> date:
    """Return the last calendar day of the given year/month."""
    if month == _DECEMBER_MONTH:
        next_month_first = date(year + 1, 1, 1)
    else:
        next_month_first = date(year, month + 1, 1)
    return next_month_first - timedelta(days=1)


def _get_loose_date_data(
    phrase: str,
    prefer: str = "current_period",
) -> tuple[date, str] | None:
    """Parse a loose date-like phrase and return its (date, granularity).

    Granularity ("year", "month", or "day") reflects how much of the phrase
    was actually specified, e.g. "april" is a "month" even though dateparser
    fills in a concrete day-of-month internally.

    Args:
        phrase: The loose date phrase to resolve (e.g. "april", "2021", "now").
        prefer: dateparser's PREFER_DATES_FROM setting, used to disambiguate
            bare month names ("past" resolves to the most recent occurrence).

    Returns:
        A (date, period) tuple, or None if the phrase isn't a recognizable date.
    """
    now = datetime.now(TORONTO_TZ).replace(tzinfo=None)
    parser = DateDataParser(
        languages=["en"],
        settings={"RELATIVE_BASE": now, "PREFER_DATES_FROM": prefer},
    )
    date_data = parser.get_date_data(phrase)
    if date_data is None or date_data.date_obj is None or date_data.period is None:
        return None

    return date_data.date_obj.date(), date_data.period


def _resolve_loose_date(
    phrase: str,
    *,
    boundary: str,
    prefer: str = "current_period",
) -> date | None:
    """Resolve a loose date-like phrase (month name, year, 'now', etc.) to a date.

    Uses the phrase's detected granularity (year/month/day) to snap to the
    start or end of that period, so partial phrases like "april" or "2021"
    produce sensible range boundaries instead of an arbitrary day-of-month.

    Args:
        phrase: The loose date phrase to resolve (e.g. "april", "2021", "now").
        boundary: "start" or "end" of the detected period.
        prefer: dateparser's PREFER_DATES_FROM setting, used to disambiguate
            bare month names ("past" resolves to the most recent occurrence).

    Returns:
        The resolved date, or None if the phrase isn't a recognizable date.
    """
    result = _get_loose_date_data(phrase, prefer)
    if result is None:
        return None
    resolved_date, period = result

    if period == "year":
        if boundary == "start":
            return date(resolved_date.year, 1, 1)
        return date(resolved_date.year, 12, 31)
    if period == "month":
        if boundary == "start":
            return date(resolved_date.year, resolved_date.month, 1)
        return _last_day_of_month(resolved_date.year, resolved_date.month)
    return resolved_date


def _is_relative_date(phrase: str) -> bool:
    """Check if the phrase represents a relative date range."""
    phrase_lower = phrase.lower()
    return any(
        marker in phrase_lower
        for marker in [
            "last",
            "previous",
            "this year",
            "this month",
            "this week",
        ]
    )


def _parse_relative_date_range(phrase: str) -> tuple[str | None, str | None]:
    """Parse relative date phrases into (start_date, end_date) strings.

    Args:
        phrase: The relative date phrase

    Returns:
        Tuple of (start_date_str, end_date_str) in YYYY-MM-DD format, or None
    """
    phrase_lower = phrase.lower()

    # Try exact phrase matches
    result = _parse_exact_date_phrase(phrase_lower)
    if result != (None, None):
        return result

    # Try pattern-based matches
    result = _parse_last_n_pattern(phrase_lower)
    if result != (None, None):
        return result

    result = _parse_last_period_pattern(phrase_lower)
    if result != (None, None):
        return result

    return None, None


def _parse_exact_date_phrase(phrase_lower: str) -> tuple[str | None, str | None]:
    """Parse exact date phrases like 'today', 'yesterday'."""
    now = datetime.now(TORONTO_TZ)
    today = now.date()

    start_date, end_date = None, None

    if phrase_lower == "yesterday":
        yesterday = today - timedelta(days=1)
        start_date, end_date = yesterday, yesterday
    elif phrase_lower == "today":
        start_date, end_date = today, today
    elif phrase_lower == "tomorrow":
        tomorrow = today + timedelta(days=1)
        start_date, end_date = tomorrow, tomorrow
    elif phrase_lower == "this week":
        days_since_monday = today.weekday()
        week_start = today - timedelta(days=days_since_monday)
        start_date, end_date = week_start, today
    elif phrase_lower == "this month":
        month_start = today.replace(day=1)
        start_date, end_date = month_start, today
    elif phrase_lower == "this year":
        year_start = today.replace(month=1, day=1)
        start_date, end_date = year_start, today

    if start_date and end_date:
        return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")
    return None, None


def _offset_from_today(count: int, unit: str) -> tuple[str, str]:
    """Resolve 'N units back from today' into a (start_date, today) string pair."""
    unit = unit.rstrip("s")  # Remove plural
    unit_map = {
        "day": timedelta(days=count),
        "week": timedelta(weeks=count),
        "month": timedelta(days=count * 30),
        "year": timedelta(days=count * 365),
    }
    today = datetime.now(TORONTO_TZ).date()
    start_date = today - unit_map[unit]
    return start_date.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


def _parse_last_n_pattern(phrase_lower: str) -> tuple[str | None, str | None]:
    """Parse 'last N days/weeks/months/years' pattern ('previous' is an alias)."""
    match = re.match(
        r"(?:last|previous)\s+(\d+)\s+(days?|weeks?|months?|years?)",
        phrase_lower,
    )
    if not match:
        return None, None
    return _offset_from_today(int(match.group(1)), match.group(2))


def _parse_ago_pattern(phrase_lower: str) -> tuple[str | None, str | None]:
    """Parse 'N days/weeks/months/years ago' pattern."""
    match = re.match(r"(\d+)\s+(days?|weeks?|months?|years?)\s+ago", phrase_lower)
    if not match:
        return None, None
    return _offset_from_today(int(match.group(1)), match.group(2))


def _parse_last_period_pattern(phrase_lower: str) -> tuple[str | None, str | None]:
    """Parse 'last month/week/year/day' pattern (without numbers, 'previous' alias)."""
    for prefix in ("last ", "previous "):
        if phrase_lower.startswith(prefix):
            rest = phrase_lower[len(prefix) :]
            break
    else:
        return None, None

    if rest == "day":
        today = datetime.now(TORONTO_TZ).date()
        yesterday = today - timedelta(days=1)
        return yesterday.strftime("%Y-%m-%d"), yesterday.strftime("%Y-%m-%d")

    if rest == "week":
        return _get_last_week_range()

    if rest == "month":
        return _get_last_month_range()

    if rest == "year":
        today = datetime.now(TORONTO_TZ).date()
        return f"{today.year - 1}-01-01", f"{today.year - 1}-12-31"

    return None, None


def _get_last_week_range() -> tuple[str, str]:
    """Get date range for last week."""
    today = datetime.now(TORONTO_TZ).date()
    today_start = today - timedelta(days=today.weekday())
    last_week_start = today_start - timedelta(weeks=1)
    last_week_end = last_week_start + timedelta(days=6)
    return (
        last_week_start.strftime("%Y-%m-%d"),
        last_week_end.strftime("%Y-%m-%d"),
    )


def _get_last_month_range() -> tuple[str, str]:
    """Get date range for last month."""
    today = datetime.now(TORONTO_TZ).date()

    if today.month == 1:
        first_day_of_prev = today.replace(year=today.year - 1, month=12, day=1)
    else:
        first_day_of_prev = today.replace(month=today.month - 1, day=1)

    # Calculate last day of previous month
    if first_day_of_prev.month == _DECEMBER_MONTH:
        next_month_first = first_day_of_prev.replace(
            year=first_day_of_prev.year + 1,
            month=1,
            day=1,
        )
    else:
        next_month_first = first_day_of_prev.replace(
            month=first_day_of_prev.month + 1,
            day=1,
        )

    last_day_of_prev = next_month_first - timedelta(days=1)

    return (
        first_day_of_prev.strftime("%Y-%m-%d"),
        last_day_of_prev.strftime("%Y-%m-%d"),
    )


def _try_parse_limit(term: str, query: ParsedQuery) -> bool:
    """Try to parse an explicit 'limit:N' filter.

    Args:
        term: The term to parse.
        query: The query object to set the limit on.

    Returns:
        True if successfully parsed as a limit filter, False otherwise.
    """
    if not term.lower().startswith("limit:"):
        return False

    value = term[len("limit:") :]
    if not value.isdigit():
        return False

    query.limit = int(value)
    return True


def _try_parse_explicit_filter(
    term: str,
    query: ParsedQuery,
    valid_columns: dict[str, str],
) -> bool:
    """Try to parse an explicit filter (column:value, column~value, etc.).

    Args:
        term: The term to parse.
        query: The query object to add the filter to.
        valid_columns: Mapping of column aliases to actual column names.

    Returns:
        True if successfully parsed as an explicit filter, False otherwise.
    """
    # Match patterns like: column:value, column~value, column>value, column>=value, etc.
    match = re.match(r"^([a-zA-Z]+)([:~><]=?)(.+)$", term)
    if not match:
        return False

    column_input, operator, value = match.groups()
    column_input_lower = column_input.lower()

    # Map column alias to actual column name
    if column_input_lower not in valid_columns:
        return False

    actual_column = valid_columns[column_input_lower]

    query.filters.append(QueryFilter(actual_column, operator, value))
    return True


def _try_parse_sort(
    term: str,
    query: ParsedQuery,
    valid_columns: dict[str, str],
) -> bool:
    """Try to parse a sort specification.

    Args:
        term: The term to parse (e.g., "sort:Ticker" or "sort:-Amount").
        query: The query object to add the sort to.
        valid_columns: Mapping of column aliases to actual column names.

    Returns:
        True if successfully parsed as a sort, False otherwise.
    """
    if not term.startswith("sort:"):
        return False

    sort_spec = term[5:]  # Remove "sort:" prefix
    descending = False

    if sort_spec.startswith("-"):
        descending = True
        sort_spec = sort_spec[1:]

    sort_column_lower = sort_spec.lower()
    if sort_column_lower not in valid_columns:
        return False

    actual_column = valid_columns[sort_column_lower]
    direction = "desc" if descending else "asc"
    query.sorts.append(QuerySort(actual_column, direction))
    return True


def _try_parse_date_filter(term: str, query: ParsedQuery) -> bool:
    """Try to parse a date-related term.

    Args:
        term: The term to parse.
        query: The query object to add the filter to.

    Returns:
        True if the term was successfully parsed as a date, False otherwise.
    """
    # Pattern for YYYY-MM-DD:YYYY-MM-DD
    range_match = re.match(r"^(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})$", term)
    if range_match:
        date_from = range_match.group(1)
        date_to = range_match.group(2)
        query.filters.append(QueryFilter(Column.Txn.TXN_DATE, ">=", date_from))
        query.filters.append(QueryFilter(Column.Txn.TXN_DATE, "<=", date_to))
        return True

    # Pattern for [>|>=|<|<=]YYYY-MM-DD (or partial dates like 2023 or 2023-05)
    comparison_match = re.match(r"^(>|>=|<|<=)(\d{4}(?:-\d{2}(?:-\d{2})?)?)$", term)
    if comparison_match:
        operator, date_str = comparison_match.groups()
        query.filters.append(QueryFilter(Column.Txn.TXN_DATE, operator, date_str))
        return True

    # Pattern for exact YYYY-MM-DD
    try:
        datetime.strptime(term, "%Y-%m-%d").replace(tzinfo=TORONTO_TZ)
    except ValueError:
        return False
    else:
        query.filters.append(QueryFilter(Column.Txn.TXN_DATE, ":", term))
        return True


def get_valid_column_names(
    live_columns: Sequence[str] | None = None,
) -> dict[str, str]:
    """Get mapping of column aliases (lowercase) to actual column names.

    Merges the fixed set of known columns with any live columns present on
    the Txns table (e.g. per-folio `optional_columns` like Description), so
    explicit filters/sorts on those columns validate only when the folio
    actually has them.

    Args:
        live_columns: Column names currently present on the Txns table.

    Returns:
        A dictionary mapping lowercase aliases to actual column names.
    """
    aliases: dict[str, str] = {
        "txnid": Column.Txn.TXN_ID,
        "txndate": Column.Txn.TXN_DATE,
        "tdate": Column.Txn.TXN_DATE,
        "date": Column.Txn.TXN_DATE,
        "action": Column.Txn.ACTION,
        "amount": Column.Txn.AMOUNT,
        "currency": Column.Txn.CURRENCY,
        "price": Column.Txn.PRICE,
        "units": Column.Txn.UNITS,
        "ticker": Column.Txn.TICKER,
        "account": Column.Txn.ACCOUNT,
        "fee": Column.Txn.FEE,
        "settledate": Column.Txn.SETTLE_DATE,
        "sdate": Column.Txn.SETTLE_DATE,
    }
    for column in live_columns or []:
        aliases.setdefault(column.lower(), column)
    return aliases
