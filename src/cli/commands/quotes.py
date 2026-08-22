"""The `folio quotes` command.

Makes the price cache inspectable.
"""

from __future__ import annotations

import typer

from app import bootstrap
from engine.cache import load_or_build
from engine.positions import held_symbols
from services.quotes_service import QuotesService
from services.symbols import load_symbol_resolver
from ui import console_error, console_info, console_print, console_success
from ui.views.dash import quotes_table


def _requested(frame_symbols: list[str], ticker: str | None) -> list[str]:
    """Narrow the folio's held symbols to the one `--ticker` asked for."""
    if ticker is None:
        return frame_symbols
    canonical = load_symbol_resolver().canonical(ticker)
    return [symbol for symbol in frame_symbols if symbol == canonical]


def manage_quotes(
    ticker: str | None = None,
    *,
    refresh: bool = False,
    clear: bool = False,
) -> None:
    """Inspect or refresh the market quote cache.

    Args:
        ticker: Limit the action to one security.
        refresh: Refetch prices from the provider.
        clear: Drop cached rows.

    Raises:
        typer.Exit: When both actions were asked for at once.
    """
    bootstrap.reload_config()

    if refresh and clear:
        console_error("Pick one of --refresh or --clear.")
        raise typer.Exit(1)

    if clear:
        _clear(ticker)
        return

    if refresh:
        _refresh(ticker)
        return

    _list(ticker)


def _held(ticker: str | None) -> list[str]:
    """Every symbol the folio holds, narrowed by `--ticker`."""
    cached = load_or_build()
    return _requested(held_symbols(cached.frame), ticker)


def _refresh(ticker: str | None) -> None:
    """Refetch prices and report what came back."""
    symbols = _held(ticker)
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


def _clear(ticker: str | None) -> None:
    """Drop cached quotes, all of them or just one."""
    dropped = QuotesService.clear([ticker] if ticker else None)
    target = ticker or "every symbol"
    console_success(f"Cleared {dropped} cached quote(s) for {target}.")


def _list(ticker: str | None) -> None:
    """Print what the cache currently holds."""
    quotes = QuotesService.cached([ticker.upper()] if ticker else None)
    if not quotes:
        console_info("No quotes cached yet. Run `folio quotes --refresh`.")
        return
    console_print(quotes_table(list(quotes.values())))


__all__ = ["manage_quotes"]
