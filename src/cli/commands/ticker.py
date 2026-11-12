"""The `folio ticker` command.

Display all details about a given ticker- its quote, fundamentals, how far its price has
moved over every range, and how each pool of the folio holds it. Works for symbols
the folio has never traded as well (showing all details except the Holding information)

With no symbol, or several, it compares them instead: one row each, with the
move over every range in a column, so each range can be read down.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, get_args

import typer

from app import bootstrap
from cli.commands.common import (
    parse_currency_choice,
    require_ledger,
    resolve_pool,
    typed_symbols,
    write_or_exit,
)
from domain import Column, Currency, Grain, QuoteStatus
from engine.performance import fetch_moves
from engine.pools import FolioValuation
from engine.ticker import (
    MARKET_SORT,
    MATRIX_SORTS,
    UnknownMatrixSortError,
    performance_rows,
    sort_performance,
    symbol_holdings,
)
from exporters.sheets import performance_sheet, ticker_sheet
from services.quote_service import QuoteService
from services.symbols import load_symbol_resolver
from term import console_error, console_info, console_warning
from ui.format import cache_badge, freshness_note
from ui.views.ticker import show_matrix, show_ticker

if TYPE_CHECKING:
    from collections.abc import Sequence

    from engine.holdings import DisplayCurrency
    from engine.ledger import Ledger
    from engine.ticker import SymbolHoldings
    from exporters.sheet_spec import Sheet

CurrencyChoice = Literal["native", "CAD", "USD", "both"]


def report_ticker(
    symbols: Sequence[str] = (),
    currency: str | None = None,
    *,
    by_account: bool = False,
    account_type: str | None = None,
    account: str | None = None,
    sort: str | None = None,
    reverse: bool = False,
    export: str | None = None,
    refresh: bool = False,
    offline: bool = False,
) -> None:
    """Show one security in focus, or compare several.

    Args:
        symbols: One symbol to look up, owned or not; several to compare; or
            none to compare every open holding. Each means the security trading
            under that name today.
        currency: One symbol only: `native` (the security's own currency),
            `CAD`, `USD` or `both`. A CAD security only ever shows CAD.
        by_account: One symbol only: a row per broker account rather than per
            account type.
        account_type: Comparing: the account type whose holdings to list, and
            whose market values and weights to show.
        account: Comparing: a single broker account instead.
        sort: Comparing: order by `market` or by a range such as `1wk`.
        reverse: Comparing: flip the sort's direction.
        export: Write what would be shown to this path instead of printing
            it. A single symbol's sheet needs a workbook; a comparison can also
            be a CSV.
        refresh: Refetch quotes, fundamentals and history, and replay the
            cost base.
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
            export=export,
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
        export=export,
        refresh=refresh,
        offline=offline,
    )


def _report_one(
    symbol: str,
    currency: str | None,
    *,
    by_account: bool,
    export: str | None,
    refresh: bool,
    offline: bool,
) -> None:
    """Show one security across the folio, with its quote and performance.

    Raises:
        typer.Exit: On an unusable request, or a symbol nobody knows.
    """
    choice: CurrencyChoice = parse_currency_choice(
        currency,
        get_args(CurrencyChoice),
    )

    canonical = typed_symbols([symbol])[0]
    quote = QuoteService.lookup(
        [canonical],
        refresh=refresh,
        offline=offline,
        fundamentals=True,
    )[canonical]

    # The quote is today's, so the rate converting it has to be too.
    ledger = require_ledger(refresh=refresh, priced=True, offline=offline)
    traded = not ledger.frame.empty and canonical in set(
        ledger.frame[Column.Ledger.SYMBOL],
    )
    if not traded and not quote.priced:
        if offline:
            console_error(
                f"Nothing is cached for '{canonical}'. Look it up without --offline.",
            )
        elif quote.status is not QuoteStatus.NOT_FOUND:
            # The lookup failed, which says nothing about the symbol.
            console_error(
                f"Could not get a quote for '{canonical}' from Yahoo Finance. "
                "Try again later.",
            )
        else:
            console_error(f"Yahoo Finance does not know '{canonical}'.")
        raise typer.Exit(1)

    moves = fetch_moves({canonical: quote}, refresh=refresh, offline=offline)[canonical]

    per_currency: list[SymbolHoldings] = []
    if traded:
        per_currency = _per_currency(
            ledger,
            canonical,
            choice,
            by_account=by_account,
            offline=offline,
        )

    family = load_symbol_resolver().family(canonical)
    aliases = [name for name in family if name != canonical]
    if export:
        note = freshness_note(ledger.computed_at, {canonical: quote})
        sheet = ticker_sheet(
            quote,
            moves,
            per_currency,
            aliases=aliases,
            by_account=by_account,
            notes=[note],
        )
        _export(sheet, export)
        return

    show_ticker(
        quote,
        moves,
        per_currency,
        aliases=aliases,
        by_account=by_account,
        badge=cache_badge(
            {canonical: quote},
            computed_at=ledger.computed_at,
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
    export: str | None,
    refresh: bool,
    offline: bool,
) -> None:
    """Compare securities over every range, one row each.

    Market values are in CAD, so they compare across currencies; prices and
    moves stay in each security's own currency, where a percentage needs none.

    Raises:
        typer.Exit: When none of the symbols asked for is known.
    """
    pool = resolve_pool(account, account_type)
    ledger = require_ledger(refresh=refresh, priced=True, offline=offline)

    valued = None
    if not ledger.frame.empty:
        valuation = FolioValuation.build(
            ledger,
            currency=Currency.CAD,
            refresh=refresh,
            offline=offline,
        )
        valued = valuation.value(pool)

    if symbols:
        wanted = typed_symbols(symbols)
        quotes = QuoteService.lookup(wanted, refresh=refresh, offline=offline)
    else:
        wanted = valued.open_symbols if valued is not None else []
        # Held securities were priced with the folio, so this reads the cache.
        quotes = QuoteService.get_quotes(wanted, offline=offline)
    if not wanted:
        console_warning(f"No open holdings in {pool.label} to compare.")
        return

    traded = set() if ledger.frame.empty else set(ledger.frame[Column.Ledger.SYMBOL])
    unknown = [
        name for name in wanted if not quotes[name].priced and name not in traded
    ]
    if unknown:
        console_warning(f"Nothing known about: {', '.join(unknown)}.")
        wanted = [name for name in wanted if name not in unknown]
    if not wanted:
        raise typer.Exit(1)

    priced = {name: quotes[name] for name in wanted}
    moves = fetch_moves(priced, refresh=refresh, offline=offline)
    rows = performance_rows(valued, priced, moves)
    if sort is not None or not symbols:
        rows = sort_performance(rows, sort or MARKET_SORT, reverse=reverse)

    pool_weight = pool.grain is not Grain.FOLIO
    if export:
        note = freshness_note(ledger.computed_at, priced)
        sheet = performance_sheet(
            rows,
            name=f"Performance - {pool.label}",
            pool_weight=pool_weight,
            notes=[note],
        )
        _export(sheet, export)
        return

    show_matrix(
        rows,
        title=pool.label,
        pool_weight=pool_weight,
        badge=cache_badge(
            priced,
            computed_at=ledger.computed_at,
            offline=offline,
            with_acb=valued is not None,
        ),
    )


def _export(sheet: Sheet, path: str) -> None:
    """Write a sheet out, choosing the format from the path's suffix.

    Raises:
        typer.Exit: If the path names a format that cannot hold the sheet.
    """
    written = write_or_exit(Path(path), [sheet])
    console_info(f"Exported {sheet.name} to {written}")


def _per_currency(
    ledger: Ledger,
    symbol: str,
    choice: CurrencyChoice,
    *,
    by_account: bool,
    offline: bool,
) -> list[SymbolHoldings]:
    """Value the security in every currency the request asked to see.

    The native view is valued first, since it says which currency the security
    trades in: a CAD security has nothing to show in USD, so any choice shows
    it in CAD, once.
    """

    def holdings_in(currency: DisplayCurrency) -> SymbolHoldings | None:
        valuation = FolioValuation.build(
            ledger,
            currency=currency,
            offline=offline,
        )
        return symbol_holdings(valuation, symbol, by_account=by_account)

    native = holdings_in("native")
    if native is None or choice in {"native", "USD"} or native.currency is Currency.CAD:
        return [native] if native is not None else []
    in_cad = holdings_in(Currency.CAD)
    shown = [in_cad] if choice == "CAD" else [native, in_cad]
    return [holdings for holdings in shown if holdings is not None]
