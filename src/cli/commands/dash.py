"""The `folio dash` command.

Prices the folio's open positions and prints them as a broker-style table, with
cash and cumulative flows in a panel above.

Two caches feed the table, the cost-base frame and the quotes, and both ages are
disclosed in the header. A stale price silently makes every market value and
unrealized gain look wrong, so it is never shown without saying how old it is.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer

from app import bootstrap
from cli.commands.common import ensure_fx_coverage, resolve_pool
from domain import Currency
from engine.cache import build, load_or_build
from engine.panels import FolioValuation, account_type_of, type_view
from engine.positions import SORT_NAMES, UnknownSortError
from exporters.excel_style import ACCOUNT_TYPE_TABS, FOLIO_TAB
from exporters.output import (
    CSV_SUFFIX,
    SingleSheetError,
    UnsupportedExportError,
    write_export,
)
from exporters.sheets import dashboard_table, pooled_dashboard_table
from term import console_error, console_info, console_warning
from ui.format import format_freshness, freshness_line
from ui.views.dash import show_by_type, show_dashboard

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from engine.panels import Panel, PoolView
    from engine.positions import ValuationCurrency
    from exporters.table import Table
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


def _export_note(computed_at: datetime | None, quotes: dict[str, Quote]) -> str:
    """Say how old both caches are, as plain text for an exported sheet."""
    quote_at, _ = _quote_age(quotes)
    empty = not any(quote.fetched_at for quote in quotes.values())
    aged = "nothing cached" if empty else format_freshness(quote_at)
    return f"Cost base {format_freshness(computed_at)}; quotes {aged}."


def _tab_color(view: PoolView) -> str | None:
    """Accent a sheet tab the way the printed panel accents its title."""
    account_type = account_type_of(view)
    if account_type is None:
        return FOLIO_TAB
    return ACCOUNT_TYPE_TABS.get(account_type)


def _sheet(panel: Panel, badge: str) -> Table:
    """Lay one panel out as a sheet of its own."""
    return dashboard_table(
        panel.holdings,
        panel.view.label,
        net_deposited=panel.flows.net_deposit_denominator,
        tab_color=_tab_color(panel.view),
        notes=[badge],
    )


def _export(panels: Sequence[Panel], path: str, badge: str) -> None:
    """Write the valued holdings out, choosing the format from the suffix.

    A workbook gives each pool a sheet. A CSV holds one table, so several pools
    flatten into one, each row naming the pool it belongs to.

    Args:
        panels: The pools to write, in the order they should appear.
        path: Where to write. A path with no suffix becomes a workbook.
        badge: The freshness line, as plain text for the sheet's notes.

    Raises:
        typer.Exit: If the path names a format that cannot be written.
    """
    target = Path(path)
    flatten = target.suffix.lower() == CSV_SUFFIX and len(panels) > 1
    if flatten:
        tables = [
            pooled_dashboard_table(
                [
                    (
                        panel.view.label,
                        panel.holdings,
                        panel.flows.net_deposit_denominator,
                    )
                    for panel in panels
                ],
                "Holdings",
                notes=[badge],
            ),
        ]
    else:
        tables = [_sheet(panel, badge) for panel in panels]

    try:
        written = write_export(target, tables)
    except (UnsupportedExportError, SingleSheetError) as error:
        console_error(str(error))
        raise typer.Exit(1) from error
    count = sum(len(table.data_rows) for table in tables)
    console_info(f"Exported {count} holding(s) to {written}")


def show_dash(
    account_type: str | None = None,
    account: str | None = None,
    currency: str | None = None,
    export: str | None = None,
    sort: str | None = None,
    *,
    by_type: bool = False,
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

    # One valuation for the whole invocation: every panel below reads its
    # rollups from here, and the quotes are fetched once rather than per panel.
    valuation = FolioValuation.build(
        cached.frame,
        result,
        currency=shown,
        refresh=refresh,
        offline=offline,
    )
    badge = _badge(cached.computed_at, valuation.quotes, offline=offline)
    note = _export_note(cached.computed_at, valuation.quotes)

    if by_type:
        panels = valuation.panels(
            [type_view(name) for name in valuation.account_types()],
            sort=sort,
            reverse=reverse,
        )
        if not panels:
            console_warning("No open positions in any account type.")
            return
        if export:
            _export(panels, export, note)
            return
        show_by_type(
            [(panel.holdings, panel.flows, panel.view.label) for panel in panels],
            wide=wide,
            show_closed=show_closed,
            badge=badge,
        )
        return

    panel = valuation.panel(
        resolve_pool(account, account_type),
        sort=sort,
        reverse=reverse,
    )

    if export:
        _export([panel], export, note)
        return

    show_dashboard(
        panel.holdings,
        panel.flows,
        panel.view.label,
        wide=wide,
        show_closed=show_closed,
        badge=badge,
    )
