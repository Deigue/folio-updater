"""The `folio value` command: every account's market value on past dates.

One date lists each account; two months list every month-end between them; a
date and a symbol list that one holding.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from typing import TYPE_CHECKING

import typer

from cli.commands.common import parse_currency_choice, require_ledger, resolve_pool
from domain import Currency
from domain.dates import today_in_toronto
from domain.text import strip_upper
from engine.fx_rates import FxRateUnavailableError
from engine.historical_prices import top_up_historical_prices
from engine.past_values import value_pools
from term import console_error, console_info, console_warning
from ui.views.value import show_holding, show_range, show_value

if TYPE_CHECKING:
    from engine.accounts import Pool
    from engine.holdings import DisplayCurrency
    from engine.ledger import Ledger
    from engine.past_values import DatedValuation

_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURRENCIES = ("CAD", "USD", "native")


def show_values(
    asked: str,
    second: str | None = None,
    *,
    currency: str | None = None,
    account_type: str | None = None,
    account: str | None = None,
    by_type: bool = False,
    offline: bool = False,
) -> None:
    """Value every account on one date, at each month-end in a range, or one holding.

    Args:
        asked: `YYYY-MM` for the month's last day, or `YYYY-MM-DD`.
        second: An end month, for a range, or a symbol, for one holding.
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
    ranged = second is not None and _MONTH.match(second) is not None
    symbol = strip_upper(second) if second is not None and not ranged else None
    if by_type and (account_type or account):
        console_error("Use --by-type on its own, not with --type or --account.")
        raise typer.Exit(1)
    scope = resolve_pool(account, account_type)
    today = today_in_toronto()

    if ranged:
        if into == "native":
            console_error("native needs a single date; use CAD or USD for a range.")
            raise typer.Exit(1)
        days, capped = _month_ends(asked, str(second), today)
        valuations = _value(days, offline=offline)
        scope = _spelled(scope, valuations[-1])
        show_range(valuations, scope, into, by_type=by_type, capped=capped)
        return

    day, capped = _resolve_day(asked, today)
    [valued] = _value([day], offline=offline)
    scope = _spelled(scope, valued)
    rows = valued.rows(scope, by_type=by_type)
    if rows is None:
        console_info(f"{_named(scope)} held nothing on {valued.on}.")
        return
    if symbol is None:
        show_value(valued, rows, into, capped=capped, by_type=by_type)
        return
    if rows.total.held(symbol) is None:
        console_info(f"{_named(scope)} held no {symbol} on {valued.on}.")
        return
    show_holding(valued, rows, symbol, into, capped=capped, by_type=by_type)


def _spelled(scope: Pool, valued: DatedValuation) -> Pool:
    """Match an account asked for to the folio's own spelling, ignoring case."""
    wanted = scope.name.casefold()
    return next(
        (
            pool
            for pool in valued.pools
            if pool.grain is scope.grain and pool.name.casefold() == wanted
        ),
        scope,
    )


def _named(scope: Pool) -> str:
    """Name the pool asked about as a sentence opens: the folio or its label."""
    return "The folio" if not scope.name else scope.label


def _value(days: list[date], *, offline: bool) -> list[DatedValuation]:
    """Top up historical prices unless offline, then value every pool on each day.

    Raises:
        typer.Exit: When no FX rate can convert a holding.
    """
    ledger: Ledger = require_ledger(offline=offline)
    if not offline:
        update = top_up_historical_prices(ledger)
        if missed := sorted(update.throttled | update.failed):
            console_warning(
                f"Price history not fetched for {', '.join(missed)}; "
                "valued from what is stored.",
            )
    try:
        return value_pools(ledger, days)
    except FxRateUnavailableError as error:
        console_error(str(error))
        raise typer.Exit(1) from error


def _month_ends(start: str, end: str, today: date) -> tuple[list[date], bool]:
    """Read two months as every month-end from the first through the last.

    Returns:
        Each month's last day, and whether the last stands in for one not closed yet.

    Raises:
        typer.Exit: When either is no month, or they run backward.
    """
    if _MONTH.match(start) is None:
        console_error("A range takes two months: YYYY-MM YYYY-MM.")
        raise typer.Exit(1)
    first, _ = _resolve_day(start, today)
    last, capped = _resolve_day(end, today)
    if first > last:
        console_error(f"{start} comes after {end}.")
        raise typer.Exit(1)
    days: list[date] = []
    year, month = first.year, first.month
    while (year, month) < (last.year, last.month):
        days.append(date(year, month, calendar.monthrange(year, month)[1]))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)  # noqa: PLR2004
    days.append(last)
    return days, capped


def _resolve_day(asked: str, today: date) -> tuple[date, bool]:
    """Read the day asked: a month means its last day.

    A day that has not closed yet takes the latest close, yesterday's.

    Returns:
        The day to value, and whether it stands in for one not closed yet.

    Raises:
        typer.Exit: When the text is no date, or names one still to come.
    """
    span = _span(asked)
    if span is None:
        console_error(f"Cannot read '{asked}'. Use YYYY-MM or YYYY-MM-DD.")
        raise typer.Exit(1)
    first, day = span
    if first > today:
        console_error(f"{asked} has not closed yet.")
        raise typer.Exit(1)
    if day >= today:
        return today - timedelta(days=1), True
    return day, False


def _span(asked: str) -> tuple[date, date] | None:
    """Read a month as its first and last days, or a day as itself twice.

    Returns:
        The first and last day named, or None when the text names no date.
    """
    try:
        if found := _MONTH.match(asked):
            year, month = int(found[1]), int(found[2])
            last = calendar.monthrange(year, month)[1]
            return date(year, month, 1), date(year, month, last)
        if _DAY.match(asked):
            day = date.fromisoformat(asked)
            return day, day
    except ValueError:
        return None
    return None
