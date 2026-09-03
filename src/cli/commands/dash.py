"""The `folio dash` command.

Prices the folio's open positions and prints them as a broker-style table, with
cash and cumulative flows in a panel above.

Two caches feed the table, the cost-base frame and the quotes, and both ages are
disclosed in the header. A stale price silently makes every market value and
unrealized gain look wrong, so it is never shown without saying how old it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from app import bootstrap, get_config
from cli.commands.common import PoolView, ensure_fx_coverage, resolve_pool
from domain import AccountType, Currency, Scope
from engine.accounts import resolve_account_type
from engine.cache import build, load_or_build
from engine.flows import build_flows, contributions_by_type_year
from engine.fx_rates import load_fx_rates
from engine.positions import (
    SORT_NAMES,
    FolioPositions,
    UnknownSortError,
    scope_rows,
)
from services.quotes_service import QuotesService
from services.symbols import load_symbol_resolver
from term import console_error, console_info, console_warning
from ui.format import freshness_line
from ui.layout.tiles import TilingLayout
from ui.views.dash import holdings_block, show_dashboard

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    from decimal import Decimal

    import pandas as pd

    from engine.flows import Flows
    from engine.positions import HoldingSet, ValuationCurrency
    from engine.types import ReplayResult
    from services.quotes_service import Quote

# Labels for the two caches the header ages.
_ACB_LABEL = "acb"
_QUOTES_LABEL = "quotes"


def _resolve_currency(requested: str | None) -> ValuationCurrency:
    """Read the `--currency` argument.

    Defaults to `native`, which keeps every holding in the currency it trades
    in and groups the table by currency. That is the view a holder recognises
    from their broker; `display.currency` deliberately does not apply here,
    since it exists to pick a single cost-base currency for `folio acb`.

    Raises:
        typer.Exit: If the argument is not a currency the app can value in.
    """
    choice = (requested or "native").strip().upper()
    if choice == "NATIVE":
        return "native"
    try:
        return Currency(choice)
    except ValueError:
        console_error(
            f"Unknown currency '{requested}'. Use CAD, USD or native.",
        )
        raise typer.Exit(1) from None


def _quote_age(quotes: dict[str, Quote]) -> tuple[datetime | None, bool]:
    """Age the quote cache as a whole: its oldest row wins.

    Returns:
        When the oldest quote was fetched (None if every one is from this
        invocation), and whether any of them is past its TTL.
    """
    fetched = [quote.fetched_at for quote in quotes.values() if quote.fetched_at]
    stale = any(quote.is_stale for quote in quotes.values())
    if not fetched:
        return None, stale
    oldest = min(fetched)
    # Anything fetched moments ago in this same run reads as fresh, not cached.
    return oldest, stale


def _badge(
    computed_at: datetime | None,
    quotes: dict[str, Quote],
    *,
    offline: bool,
) -> str:
    """Compose the two-cache freshness line."""
    quote_at, stale = _quote_age(quotes)
    empty = not any(quote.fetched_at for quote in quotes.values())
    parts = [(_ACB_LABEL, computed_at), (_QUOTES_LABEL, quote_at)]
    line = freshness_line(
        parts,
        warn={_QUOTES_LABEL} if stale and not empty else set(),
        missing={_QUOTES_LABEL} if empty else set(),
    )
    return f"{line} [dim](offline)[/dim]" if offline else line


def _account_type_of(view: PoolView) -> AccountType | None:
    """Name the account type a pool is, when it is exactly one.

    Contribution room only means something for a single tax type, so a
    portfolio-wide view has none.
    """
    if view.scope is Scope.TYPE:
        try:
            return AccountType(view.pool)
        except ValueError:  # pragma: no cover - resolve_pool already validated
            return None
    if view.scope is Scope.ACCOUNT:
        return resolve_account_type(view.pool)
    return None


@dataclass(frozen=True)
class _Request:
    """One dashboard invocation's inputs, past argument parsing.

    `positions` carries the rollups every panel reads from, so a dashboard
    showing several pools derives each one once. `contributions` is here for
    the same reason: the room line is a single scan of the frame however many
    panels ask for it. `folio_market` is the CAD denominator behind `Folio%`,
    which is a share of the whole portfolio however narrow the panel is and
    whatever currency it is displayed in.
    """

    positions: FolioPositions
    result: ReplayResult
    currency: ValuationCurrency
    contributions: Mapping[AccountType, Mapping[int, Decimal]]
    folio_market: Decimal | None = None
    sort: str | None = None
    reverse: bool = False
    wide: bool = False
    show_closed: bool = False

    @property
    def frame(self) -> pd.DataFrame:
        """The replayed master frame the panels are read from."""
        return self.positions.frame


def _panel(request: _Request, view: PoolView) -> tuple[HoldingSet, Flows]:
    """Build one scope's holdings and flows together."""
    holdings = request.positions.holdings(
        scope=view.scope,
        pool=view.pool,
        currency=request.currency,
        folio_market=request.folio_market,
        sort=request.sort,
        reverse=request.reverse,
    )
    flows = build_flows(
        request.result,
        scope=view.scope,
        pool=view.pool,
        label=view.label,
        account_type=_account_type_of(view),
        room=get_config().contribution_room,
        contributions=request.contributions,
    )
    return holdings, flows


def _export(holdings: HoldingSet, path: str) -> None:
    """Write the valued holdings out, choosing the format from the suffix."""
    import pandas as pd  # noqa: PLC0415 - only needed on the export path

    target = Path(path)
    frame = pd.DataFrame([vars(holding) for holding in holdings.holdings])
    if target.suffix.lower() in {".xlsx", ".xls"}:
        frame.to_excel(target, index=False)
    else:
        frame.to_csv(target, index=False)
    console_info(f"Exported {len(frame)} holding(s) to {target}")


def _account_types(frame: pd.DataFrame) -> list[str]:
    """Every account type that still holds an open position, in a stable order."""
    types: list[str] = []
    for name in sorted({str(value) for value in frame["AcctType"].dropna()}):
        rows = scope_rows(frame, Scope.TYPE, name)
        if not rows.empty:
            types.append(name)
    return types


def show_dash(
    account_type: str | None = None,
    account: str | None = None,
    currency: str | None = None,
    export: str | None = None,
    sort: str | None = None,
    *,
    by_type: bool = False,
    folio: bool = False,
    wide: bool = False,
    show_closed: bool = False,
    reverse: bool = False,
    refresh: bool = False,
    offline: bool = False,
) -> None:
    """Show the portfolio dashboard.

    Args:
        account_type: Report the pooled figures for one account type.
        account: Report a single broker account instead.
        currency: `CAD`, `USD` or `native`.
        export: Write the valued holdings to this path instead of printing.
        sort: Order by this column instead of by market value.
        by_type: Tile one panel per account type.
        folio: Report the portfolio-wide pool, the default anyway.
        wide: Show the wide-only columns.
        show_closed: Break the aggregate "Closed" row open into one row per
            closed position, instead of one summed line.
        reverse: Flip the sort's natural direction.
        refresh: Refetch quotes and rebuild the cost-base cache.
        offline: Never touch the network.

    Raises:
        typer.Exit: On an unusable request.
    """
    bootstrap.reload_config()

    if by_type and (account or account_type):
        console_error("--by-type shows every type, so it takes no --type or --account.")
        raise typer.Exit(1)

    shown = _resolve_currency(currency)
    if sort is not None and sort.strip().lower() not in SORT_NAMES:
        console_error(str(UnknownSortError(sort)))
        raise typer.Exit(1)

    if not offline:
        # Quotes are today's, so the rate that converts them has to be too.
        ensure_fx_coverage(through_today=True)

    cached = load_or_build(refresh=refresh)
    if cached.frame.empty:
        console_warning("No transactions to build a dashboard from.")
        return

    result = cached.result or build().result
    if result is None:  # pragma: no cover - `build` always replays
        console_error("Could not replay the folio.")
        raise typer.Exit(1)

    # One object for the whole invocation: every panel below reads its rollups
    # from here, and the FX table is read once rather than once per panel.
    positions = FolioPositions(cached.frame, {}, load_fx_rates())
    quotes = _quotes(positions, refresh=refresh, offline=offline)
    positions.price(quotes)

    badge = _badge(cached.computed_at, quotes, offline=offline)
    request = _Request(
        positions=positions,
        result=result,
        currency=shown,
        contributions=contributions_by_type_year(cached.frame),
        folio_market=positions.market_value(),
        sort=sort,
        reverse=reverse,
        wide=wide,
        show_closed=show_closed,
    )

    if by_type:
        _show_by_type(request, badge)
        return

    view = resolve_pool(account, account_type, folio=folio)
    holdings, flows = _panel(request, view)

    if export:
        _export(holdings, export)
        return

    show_dashboard(
        holdings,
        flows,
        view.label,
        wide=wide,
        show_closed=show_closed,
        badge=badge,
    )


def _quotes(
    positions: FolioPositions,
    *,
    refresh: bool,
    offline: bool,
) -> dict[str, Quote]:
    """Price every open position, once, for the whole folio."""
    symbols = positions.held_symbols()
    if not symbols:
        return {}
    return QuotesService.get_quotes(
        symbols,
        load_symbol_resolver(),
        refresh=refresh,
        offline=offline,
    )


def _show_by_type(request: _Request, badge: str) -> None:
    """Tile one panel per account type across the terminal."""
    from term import console_print  # noqa: PLC0415 - printed only on this path

    console_print(badge)

    # Every type is about to be reported, so split the frame once here rather
    # than narrowing it again for each panel.
    request.positions.prime(Scope.TYPE)

    blocks = []
    for name in _account_types(request.frame):
        view = PoolView(Scope.TYPE, name, name.replace("_", "-"))
        holdings, flows = _panel(request, view)
        if not holdings.holdings and not holdings.closed:
            continue
        blocks.append(
            holdings_block(
                holdings,
                flows,
                view.label,
                wide=request.wide,
                show_closed=request.show_closed,
            ),
        )

    if not blocks:
        console_warning("No open positions in any account type.")
        return
    TilingLayout(blocks).render()
