"""The `folio quotes` command.

Makes the price cache inspectable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from cli.commands.common import require_ledger
from db import historical_prices
from domain.text import unique_upper
from engine.historical_prices import top_up_historical_prices
from engine.ledger import LedgerUnavailableError, open_ledger
from services.quote_service import QuoteService
from term import console_error, console_info, console_print, console_success
from ui.views.quotes import historical_prices_table, quotes_table

if TYPE_CHECKING:
    from collections.abc import Sequence


def manage_quotes(
    symbols: Sequence[str] = (),
    *,
    refresh: bool = False,
    clear: bool = False,
    history: bool = False,
) -> None:
    """Inspect or refresh the market quote cache.

    Args:
        symbols: Limit the action to these securities, each the one trading
            under that name today.
        refresh: Refetch prices from the provider.
        clear: Drop cached rows, price history included.
        history: Top up the stored historical prices and show their coverage.

    Raises:
        typer.Exit: When more than one action was asked for at once.
    """
    if refresh + clear + history > 1:
        console_error("Pick one of --refresh, --clear or --history.")
        raise typer.Exit(1)

    # A typed symbol means what trades under that name today: no rename is followed.
    wanted = unique_upper(symbols)

    if clear:
        _clear(wanted)
        return

    if refresh:
        _refresh(wanted)
        return

    if history:
        _history(wanted)
        return

    _list(wanted)


def _held(wanted: Sequence[str]) -> list[str]:
    """Every symbol the folio holds, narrowed to the ones asked for."""
    held = require_ledger().held_symbols
    if not wanted:
        return held
    return [symbol for symbol in held if symbol in wanted]


def _refresh(wanted: Sequence[str]) -> None:
    """Refetch prices and report what came back."""
    symbols = _held(wanted)
    if not symbols:
        console_info("No open holdings to price.")
        return

    result = QuoteService.refresh(symbols)
    console_success(
        f"Fetched {result.fetched} quote(s); "
        f"{result.not_found} not found, {len(result.failed)} failed.",
    )
    if result.used_fallback:
        console_info(
            "The provider was throttling, so these are daily closes rather "
            "than intraday prices.",
        )


def _history(wanted: Sequence[str]) -> None:
    """Top up every traded symbol's historical prices, then show what is stored."""
    update = top_up_historical_prices(require_ledger())
    console_success(
        f"Fetched {update.fetched} symbol(s), stored {update.stored} new price(s); "
        f"{len(update.no_data)} with no data, {len(update.throttled)} throttled, "
        f"{len(update.failed)} failed.",
    )
    summaries = historical_prices.summaries(wanted or None)
    if not summaries:
        console_info("No historical prices stored yet.")
        return
    console_print(historical_prices_table(summaries))


def _clear(wanted: Sequence[str]) -> None:
    """Drop cached quotes, all of them or just the ones asked for."""
    dropped = QuoteService.clear(wanted or None)
    target = ", ".join(wanted) or "every symbol"
    console_success(f"Cleared {dropped} cached quote(s) for {target}.")


def _list(wanted: Sequence[str]) -> None:
    """Print what the cache currently holds."""
    quotes = QuoteService.cached(wanted or None)
    if not quotes:
        console_info("No quotes cached yet. Run `folio quotes --refresh`.")
        return
    console_print(quotes_table(list(quotes.values()), held=_held_now()))


def _held_now() -> set[str] | None:
    """Every symbol the folio holds, or None when it cannot be replayed.

    Listing the cache must not fail over a folio problem, so an unreplayable
    folio just leaves the `Held` column out.
    """
    try:
        return set(open_ledger().held_symbols)
    except LedgerUnavailableError:
        return None


__all__ = ["manage_quotes"]
