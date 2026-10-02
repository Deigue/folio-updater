"""The `folio ticker` command.

Display all details about a given ticker- its quote, fundamentals, how far its price has
moved over every range, and how each pool of the folio holds it. Works for symbols
the folio has never traded as well (showing all details except the Holding information)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import typer

from app import bootstrap
from cli.commands.common import (
    cache_badge,
    ensure_fx_coverage,
    load_folio,
    replay_result,
)
from domain import Currency
from engine.panels import FolioValuation
from engine.performance import price_moves
from engine.ticker import symbol_position
from services.quotes_service import QuotesService
from services.symbols import load_symbol_resolver
from term import console_error
from ui.views.ticker import show_ticker

if TYPE_CHECKING:
    from engine.cache import CachedFrame
    from engine.positions import ValuationCurrency
    from engine.ticker import SymbolPosition

CurrencyChoice = Literal["native", "CAD", "USD", "both"]
_CHOICES: tuple[CurrencyChoice, ...] = ("native", "CAD", "USD", "both")


def report_ticker(
    symbol: str,
    currency: str | None = None,
    *,
    by_account: bool = False,
    refresh: bool = False,
    offline: bool = False,
) -> None:
    """Show one security across the folio, with its quote and performance.

    Args:
        symbol: The symbol to look up, owned or not. An old alias resolves to
            the symbol it was renamed to.
        currency: `native` (the security's own currency), `CAD`, `USD` or
            `both`. A CAD security only ever shows CAD.
        by_account: One row per broker account rather than per account type.
        refresh: Refetch the quote, fundamentals and history, and rebuild the
            cost-base cache.
        offline: Never touch the network; show whatever is cached.

    Raises:
        typer.Exit: On an unusable request, or a symbol nobody knows.
    """
    bootstrap.reload_config()
    choice = _currency_choice(currency)

    resolver = load_symbol_resolver()
    canonical = resolver.canonical(symbol)
    quote = QuotesService.get_quotes(
        [canonical],
        resolver,
        refresh=refresh,
        offline=offline,
        fundamentals=True,
    )[canonical]

    if not offline:
        # The quote is today's, so the rate converting it has to be too.
        ensure_fx_coverage(through_today=True)
    cached = load_folio(refresh=refresh)
    traded = not cached.frame.empty and canonical in set(cached.frame["Symbol"])
    if not traded and not quote.priced:
        if offline:
            console_error(
                f"Nothing is cached for '{canonical}'. Look it up without --offline.",
            )
        else:
            # Asking created a "not found" row in the cache. That row is how a
            # holding Yahoo cannot price avoids being re-asked on every run, but
            # for a symbol nobody holds it is just a typo, kept forever.
            QuotesService.clear([canonical])
            console_error(f"Yahoo Finance does not know '{canonical}'.")
        raise typer.Exit(1)

    history = QuotesService.history(
        [canonical],
        resolver,
        refresh=refresh,
        offline=offline,
    ).get(canonical, {})
    intraday = QuotesService.intraday([canonical], resolver, offline=offline)

    positions: list[SymbolPosition] = []
    if traded:
        positions = _positions(
            cached,
            canonical,
            choice,
            by_account=by_account,
            offline=offline,
        )

    show_ticker(
        quote,
        price_moves(quote, history, intraday.get(canonical, {})),
        positions,
        aliases=[name for name in resolver.family(canonical) if name != canonical],
        by_account=by_account,
        badge=cache_badge(
            {canonical: quote},
            computed_at=cached.computed_at,
            offline=offline,
            with_acb=traded,
        ),
    )


def _currency_choice(requested: str | None) -> CurrencyChoice:
    """Read the `--currency` argument.

    Raises:
        typer.Exit: If it is not a choice this command offers.
    """
    wanted = (requested or "native").strip().lower()
    for choice in _CHOICES:
        if choice.lower() == wanted:
            return choice
    console_error(f"Unknown currency '{requested}'. Use native, CAD, USD or both.")
    raise typer.Exit(1)


def _positions(
    cached: CachedFrame,
    symbol: str,
    choice: CurrencyChoice,
    *,
    by_account: bool,
    offline: bool,
) -> list[SymbolPosition]:
    """Value the security in every currency the request asked to see.

    The native view is valued first, since it says which currency the security
    trades in: a CAD security has nothing to show in USD, so any choice shows
    it in CAD, once.
    """
    result = replay_result(cached)

    def valued(currency: ValuationCurrency) -> SymbolPosition | None:
        valuation = FolioValuation.build(
            cached.frame,
            result,
            currency=currency,
            offline=offline,
        )
        return symbol_position(valuation, symbol, by_account=by_account)

    native = valued("native")
    if native is None or choice in {"native", "USD"} or native.currency is Currency.CAD:
        return [native] if native is not None else []
    in_cad = valued(Currency.CAD)
    shown = [in_cad] if choice == "CAD" else [native, in_cad]
    return [position for position in shown if position is not None]
