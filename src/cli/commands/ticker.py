"""The `folio ticker` command.

Display all details about a given ticker- its quote, fundamentals, how far its price has
moved over every range, and how each pool of the folio holds it. Works for symbols
the folio has never traded as well (showing all details except the Holding information)

With no symbol, or several, it compares them instead: one row each, with the
move over every range in a column, so each range can be read down.
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
    resolve_pool,
)
from domain import Currency, Scope
from engine.panels import FolioValuation
from engine.performance import price_moves
from engine.ticker import (
    MARKET_SORT,
    MATRIX_SORTS,
    UnknownMatrixSortError,
    performance_rows,
    sort_performance,
    symbol_position,
)
from services.quotes_service import QuotesService
from services.symbols import load_symbol_resolver
from term import console_error, console_warning
from ui.views.ticker import show_matrix, show_ticker

if TYPE_CHECKING:
    from collections.abc import Sequence

    from engine.cache import CachedFrame
    from engine.positions import ValuationCurrency
    from engine.ticker import SymbolPosition

CurrencyChoice = Literal["native", "CAD", "USD", "both"]
_CHOICES: tuple[CurrencyChoice, ...] = ("native", "CAD", "USD", "both")


def report_ticker(
    symbols: Sequence[str] = (),
    currency: str | None = None,
    *,
    by_account: bool = False,
    account_type: str | None = None,
    account: str | None = None,
    sort: str | None = None,
    reverse: bool = False,
    refresh: bool = False,
    offline: bool = False,
) -> None:
    """Show one security in focus, or compare several.

    Args:
        symbols: One symbol to look up, owned or not; several to compare; or
            none to compare every open position. An old alias resolves to the
            symbol it was renamed to.
        currency: One symbol only: `native` (the security's own currency),
            `CAD`, `USD` or `both`. A CAD security only ever shows CAD.
        by_account: One symbol only: a row per broker account rather than per
            account type.
        account_type: Comparing: the account type whose holdings to list, and
            whose market values and weights to show.
        account: Comparing: a single broker account instead.
        sort: Comparing: order by `market` or by a range such as `1wk`.
        reverse: Comparing: flip the sort's direction.
        refresh: Refetch quotes, fundamentals and history, and rebuild the
            cost-base cache.
        offline: Never touch the network; show whatever is cached.

    Raises:
        typer.Exit: On an unusable request, or a symbol nobody knows.
    """
    bootstrap.reload_config()
    if len(symbols) == 1:
        if account or account_type or sort or reverse:
            console_error(
                "--type, --account, --sort and --reverse arrange a comparison. "
                "Name no symbol, or several, to use them.",
            )
            raise typer.Exit(1)
        _report_one(
            symbols[0],
            currency,
            by_account=by_account,
            refresh=refresh,
            offline=offline,
        )
        return

    if by_account or currency:
        console_error("--by-account and --currency apply to a single symbol.")
        raise typer.Exit(1)
    if sort is not None and sort.strip().lower() not in MATRIX_SORTS:
        console_error(str(UnknownMatrixSortError(sort)))
        raise typer.Exit(1)
    _report_matrix(
        symbols,
        account_type=account_type,
        account=account,
        sort=sort,
        reverse=reverse,
        refresh=refresh,
        offline=offline,
    )


def _report_one(
    symbol: str,
    currency: str | None,
    *,
    by_account: bool,
    refresh: bool,
    offline: bool,
) -> None:
    """Show one security across the folio, with its quote and performance.

    Raises:
        typer.Exit: On an unusable request, or a symbol nobody knows.
    """
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


def _report_matrix(
    symbols: Sequence[str],
    *,
    account_type: str | None,
    account: str | None,
    sort: str | None,
    reverse: bool,
    refresh: bool,
    offline: bool,
) -> None:
    """Compare securities over every range, one row each.

    Market values are in CAD, so they compare across currencies; prices and
    moves stay in each security's own currency, where a percentage needs none.

    Raises:
        typer.Exit: When none of the symbols asked for is known.
    """
    resolver = load_symbol_resolver()
    view = resolve_pool(account, account_type)
    if not offline:
        ensure_fx_coverage(through_today=True)
    cached = load_folio(refresh=refresh)

    panel = None
    if not cached.frame.empty:
        valuation = FolioValuation.build(
            cached.frame,
            replay_result(cached),
            currency=Currency.CAD,
            refresh=refresh,
            offline=offline,
        )
        panel = valuation.panel(view)

    if symbols:
        wanted = list(dict.fromkeys(resolver.canonical(name) for name in symbols))
    else:
        held = panel.holdings.holdings if panel is not None else []
        wanted = [holding.symbol for holding in held]
    if not wanted:
        console_warning(f"No open positions in {view.label} to compare.")
        return

    # Held securities were priced with the folio; only named ones may be new.
    quotes = QuotesService.get_quotes(
        wanted,
        resolver,
        refresh=refresh and bool(symbols),
        offline=offline,
    )
    traded = set() if cached.frame.empty else set(cached.frame["Symbol"])
    unknown = [
        name for name in wanted if not quotes[name].priced and name not in traded
    ]
    if unknown:
        if not offline:
            QuotesService.clear(unknown)
        console_warning(f"Nothing known about: {', '.join(unknown)}.")
        wanted = [name for name in wanted if name not in unknown]
    if not wanted:
        raise typer.Exit(1)

    history = QuotesService.history(wanted, resolver, refresh=refresh, offline=offline)
    intraday = QuotesService.intraday(wanted, resolver, offline=offline)
    moves = {
        name: price_moves(quotes[name], history.get(name, {}), intraday.get(name, {}))
        for name in wanted
    }
    rows = performance_rows(panel, {name: quotes[name] for name in wanted}, moves)
    if sort is not None or not symbols:
        rows = sort_performance(rows, sort or MARKET_SORT, reverse=reverse)

    show_matrix(
        rows,
        title=view.label,
        pool_weight=view.scope is not Scope.FOLIO,
        badge=cache_badge(
            {name: quotes[name] for name in wanted},
            computed_at=cached.computed_at,
            offline=offline,
            with_acb=panel is not None,
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
