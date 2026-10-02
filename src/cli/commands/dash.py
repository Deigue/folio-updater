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
from cli.commands.common import (
    cache_badge,
    ensure_fx_coverage,
    freshness_note,
    load_folio,
    replay_result,
    resolve_pool,
)
from domain import Currency
from engine.panels import FolioValuation, type_view
from engine.positions import SORT_NAMES, UnknownSortError
from exporters.output import (
    CSV_SUFFIX,
    SingleSheetError,
    UnsupportedExportError,
    write_export,
)
from exporters.sheets import panel_table, pooled_dashboard_table
from term import console_error, console_info, console_warning
from ui.views.dash import show_by_type, show_dashboard

if TYPE_CHECKING:
    from collections.abc import Sequence

    from engine.panels import Panel
    from engine.positions import ValuationCurrency


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
        tables = [pooled_dashboard_table(panels, "Holdings", notes=[badge])]
    else:
        tables = [panel_table(panel, notes=[badge]) for panel in panels]

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

    cached = load_folio(refresh=refresh)
    if cached.frame.empty:
        console_warning("No transactions to build a dashboard from.")
        return

    result = replay_result(cached)

    # One valuation for the whole invocation: every panel below reads its
    # rollups from here, and the quotes are fetched once rather than per panel.
    valuation = FolioValuation.build(
        cached.frame,
        result,
        currency=shown,
        refresh=refresh,
        offline=offline,
    )
    badge = cache_badge(
        valuation.quotes,
        computed_at=cached.computed_at,
        offline=offline,
    )
    note = freshness_note(cached.computed_at, valuation.quotes)

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
