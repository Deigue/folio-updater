"""The `folio quotes` command.

Makes the price cache inspectable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from app import bootstrap
from cli.commands.common import load_folio
from engine.cache import load_or_build
from engine.fx_rates import FxRateUnavailableError
from engine.positions import held_symbols
from services.quotes_service import QuotesService
from services.symbols import load_symbol_resolver
from term import console_error, console_info, console_print, console_success
from ui.views.dash import quotes_table

if TYPE_CHECKING:
    from collections.abc import Sequence


def manage_quotes(
    symbols: Sequence[str] = (),
    *,
    refresh: bool = False,
    clear: bool = False,
) -> None:
    """Inspect or refresh the market quote cache.

    Args:
        symbols: Limit the action to these securities. An old alias acts on
            the symbol it was renamed to.
        refresh: Refetch prices from the provider.
        clear: Drop cached rows, price history included.

    Raises:
        typer.Exit: When both actions were asked for at once.
    """
    bootstrap.reload_config()

    if refresh and clear:
        console_error("Pick one of --refresh or --clear.")
        raise typer.Exit(1)

    resolver = load_symbol_resolver()
    wanted = list(dict.fromkeys(resolver.canonical(symbol) for symbol in symbols))

    if clear:
        _clear(wanted)
        return

    if refresh:
        _refresh(wanted)
        return

    _list(wanted)


def _held(wanted: Sequence[str]) -> list[str]:
    """Every symbol the folio holds, narrowed to the ones asked for."""
    held = held_symbols(load_folio().frame)
    if not wanted:
        return held
    return [symbol for symbol in held if symbol in wanted]


def _refresh(wanted: Sequence[str]) -> None:
    """Refetch prices and report what came back."""
    symbols = _held(wanted)
    if not symbols:
        console_info("No open positions to price.")
        return

    result = QuotesService.refresh(symbols, load_symbol_resolver())
    console_success(
        f"Fetched {result.fetched} quote(s); "
        f"{result.not_found} not found, {result.failed} failed.",
    )
    if result.used_fallback:
        console_info(
            "The provider was throttling, so these are daily closes rather "
            "than intraday prices.",
        )


def _clear(wanted: Sequence[str]) -> None:
    """Drop cached quotes, all of them or just the ones asked for."""
    dropped = QuotesService.clear(wanted or None)
    target = ", ".join(wanted) or "every symbol"
    console_success(f"Cleared {dropped} cached quote(s) for {target}.")


def _list(wanted: Sequence[str]) -> None:
    """Print what the cache currently holds."""
    quotes = QuotesService.cached(wanted or None)
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
        return set(held_symbols(load_or_build().frame))
    except FxRateUnavailableError:
        return None


__all__ = ["manage_quotes"]
