"""The `folio value` command: every account's market value on past dates.

One date lists each account; two months list every month-end between them; a
date and a symbol list that one holding.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import TYPE_CHECKING

import typer

from cli.commands.common import parse_currency_choice, require_ledger, resolve_pool
from domain import Currency
from domain.dates import month_end, today_in_toronto
from domain.text import strip_upper
from engine.fx_rates import FxRateUnavailableError
from engine.historical_prices import latest_covered_day, top_up_historical_prices
from engine.past_values import value_pools
from term import console_error, console_info, console_warning
from ui.views.value import show_holding, show_months, show_value

if TYPE_CHECKING:
    from engine.accounts import Pool
    from engine.holdings import DisplayCurrency
    from engine.ledger import Ledger
    from engine.past_values import DatedValuation

_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURRENCIES = ("CAD", "USD", "native")


def run_value(
    asked: str,
    end_or_symbol: str | None = None,
    *,
    currency: str | None = None,
    account_type: str | None = None,
    account: str | None = None,
    by_type: bool = False,
    offline: bool = False,
) -> None:
    """Value every account on a date or at each month-end, or one holding on a date.

    Args:
        asked: `YYYY-MM` for the month's last day, or `YYYY-MM-DD`.
        end_or_symbol: An end month, for every month-end through it, or a
            symbol, for one holding.
        currency: CAD (the default), USD, or native to keep each currency apart.
        account_type: Narrow to one account type's accounts.
        account: Narrow to one account.
        by_type: List each account type's pool instead of each account.
        offline: Value from the prices stored, fetching none.

    Raises:
        typer.Exit: When the request cannot be valued.
    """
    choice = parse_currency_choice(currency, _CURRENCIES)
    into: DisplayCurrency = "native" if choice == "native" else Currency(choice)
    end = end_or_symbol if end_or_symbol and _MONTH.match(end_or_symbol) else None
    symbol = strip_upper(end_or_symbol) if end_or_symbol and end is None else None
    if by_type and (account_type or account):
        console_error("Use --by-type on its own, not with --type or --account.")
        raise typer.Exit(1)
    if end is not None and into == "native":
        console_error("native needs a single date; use CAD or USD for month-ends.")
        raise typer.Exit(1)
    pool = resolve_pool(account, account_type)
    today = today_in_toronto()
    asked_span = _read(asked, today)
    end_span = None if end is None else _read(end, today)

    ledger = _ledger(offline=offline)
    latest = latest_covered_day(ledger, by=today - timedelta(days=1))
    if end is not None and end_span is not None:
        days, capped = _month_ends((asked, asked_span), (end, end_span), today, latest)
        valuations = _valued(ledger, days)
        pool = _spelled(pool, valuations[-1])
        show_months(valuations, pool, Currency(choice), by_type=by_type, capped=capped)
        return

    capped = asked_span[1] >= today
    [valued] = _valued(ledger, [latest if capped else asked_span[1]])
    pool = _spelled(pool, valued)
    rows = valued.rows(pool, by_type=by_type)
    if rows is None:
        console_info(f"{pool.label} held nothing on {valued.on}.")
    elif symbol is None:
        show_value(valued, rows, into, capped=capped, by_type=by_type)
    elif rows.total.held(symbol) is None:
        console_info(f"{pool.label} held no {symbol} on {valued.on}.")
    else:
        show_holding(valued, rows, symbol, into, capped=capped, by_type=by_type)


def _spelled(pool: Pool, valued: DatedValuation) -> Pool:
    """Match an account asked for to the folio's own spelling, ignoring case."""
    wanted = pool.name.casefold()
    return next(
        (
            known
            for known in valued.pools
            if known.grain is pool.grain and known.name.casefold() == wanted
        ),
        pool,
    )


def _ledger(*, offline: bool) -> Ledger:
    """Open the ledger, topping up historical prices first unless offline."""
    ledger = require_ledger(offline=offline)
    if not offline:
        update = top_up_historical_prices(ledger)
        if missed := sorted(update.missed):
            console_warning(
                f"Historical prices not fetched for {', '.join(missed)}; "
                "valued from what is stored.",
            )
    return ledger


def _valued(ledger: Ledger, days: list[date]) -> list[DatedValuation]:
    """Value every pool on each day.

    Raises:
        typer.Exit: When no FX rate can convert a holding.
    """
    try:
        return value_pools(ledger, days)
    except FxRateUnavailableError as error:
        console_error(str(error))
        raise typer.Exit(1) from error


def _month_ends(
    start: tuple[str, tuple[date, date]],
    end: tuple[str, tuple[date, date]],
    today: date,
    latest: date,
) -> tuple[list[date], bool]:
    """List every month-end from one month through another, each as typed and read.

    A month not closed yet takes the latest close, and one none of whose days
    has closed is left out.

    Returns:
        Each month's day to value, and whether the last stands in for one not
        closed yet.

    Raises:
        typer.Exit: When the first is no month, they run backward, or no month
            has closed.
    """
    (start_text, (start_day, _)), (end_text, (end_day, _)) = start, end
    if _MONTH.match(start_text) is None:
        console_error("Month-ends take two months: YYYY-MM YYYY-MM.")
        raise typer.Exit(1)
    if start_day > end_day:
        console_error(f"{start_text} comes after {end_text}.")
        raise typer.Exit(1)
    days: list[date] = []
    capped = False
    year, month = start_day.year, start_day.month
    while (year, month) <= (end_day.year, end_day.month):
        last = month_end(year, month)
        open_month = last >= today
        day = latest if open_month else last
        if (day.year, day.month) == (year, month):
            days.append(day)
            capped = open_month
        else:
            console_info(f"{year}-{month:02d} has no close yet, so it is left out.")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)  # noqa: PLR2004
    if not days:
        console_error(f"{end_text} has no close yet.")
        raise typer.Exit(1)
    return days, capped


def _read(asked: str, today: date) -> tuple[date, date]:
    """Read a month as its first and last days, or a day as itself twice.

    Raises:
        typer.Exit: When the text is no date, or names one still to come.
    """
    span = _span(asked)
    if span is None:
        console_error(f"Cannot read '{asked}'. Use YYYY-MM or YYYY-MM-DD.")
        raise typer.Exit(1)
    if span[0] > today:
        console_error(f"{asked} has not closed yet.")
        raise typer.Exit(1)
    return span


def _span(asked: str) -> tuple[date, date] | None:
    """Read a month as its first and last days, or a day as itself twice.

    Returns:
        The first and last day named, or None when the text names no date.
    """
    try:
        if found := _MONTH.match(asked):
            year, month = int(found[1]), int(found[2])
            return date(year, month, 1), month_end(year, month)
        if _DAY.match(asked):
            day = date.fromisoformat(asked)
            return day, day
    except ValueError:
        return None
    return None
