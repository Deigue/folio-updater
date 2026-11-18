"""The `folio value` command: every account's market value on a past date."""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

import typer

from cli.commands.common import parse_currency_choice, require_ledger
from domain import Currency
from domain.dates import today_in_toronto
from engine.accounts import FOLIO
from engine.fx_rates import FxRateUnavailableError
from engine.historical_prices import top_up_historical_prices
from engine.past_values import value_pools
from term import console_error, console_info, console_warning
from ui.views.value import show_value

_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURRENCIES = ("CAD", "USD")


def show_values(asked: str, *, currency: str | None, offline: bool) -> None:
    """Value every account on a month's last day or on one day.

    Args:
        asked: `YYYY-MM` for the month's last day, or `YYYY-MM-DD`.
        currency: The currency to convert every figure into, CAD by default.
        offline: Value from the prices stored, fetching none.

    Raises:
        typer.Exit: When the day cannot be valued.
    """
    into = Currency(parse_currency_choice(currency, _CURRENCIES))
    day, capped = _resolve_day(asked, today_in_toronto())
    ledger = require_ledger(offline=offline)
    if not offline:
        update = top_up_historical_prices(ledger)
        if missed := sorted(update.throttled | update.failed):
            console_warning(
                f"Price history not fetched for {', '.join(missed)}; "
                "valued from what is stored.",
            )
    try:
        [valued] = value_pools(ledger, [day])
    except FxRateUnavailableError as error:
        console_error(str(error))
        raise typer.Exit(1) from error
    if FOLIO not in valued.pools:
        console_info(f"The folio held nothing on {valued.on}.")
        return
    show_value(valued, into, capped=capped)


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
