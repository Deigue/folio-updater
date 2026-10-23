"""The `folio dash` command.

Prices the folio's open holdings and prints them as a broker-style table, with
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
    parse_currency_choice,
    require_ledger,
    resolve_pool,
    write_or_exit,
)
from domain import Currency
from engine.holdings import SORT_NAMES, UnknownSortError
from engine.panels import FolioValuation, type_view
from exporters.output import CSV_SUFFIX
from exporters.sheets import panel_table, pooled_dashboard_table
from term import console_error, console_info, console_warning
from ui.format import cache_badge, freshness_note
from ui.views.dash import show_by_type, show_dashboard

if TYPE_CHECKING:
    from collections.abc import Sequence

    from engine.holdings import DisplayCurrency
    from engine.panels import Panel

_CURRENCIES = ("native", "CAD", "USD")


def _resolve_currency(requested: str | None) -> DisplayCurrency:
    """Read the `--currency` argument.

    Defaults to `native`, which keeps every holding in the currency it trades
    in and groups the table by currency. That is the view a holder recognises
    from their broker; `display.currency` deliberately does not apply here,
    since it exists to pick a single cost-base currency for `folio acb`.

    Raises:
        typer.Exit: If the argument is not a currency the app can value in.
    """
    choice = parse_currency_choice(requested, _CURRENCIES)
    return "native" if choice == "native" else Currency(choice)


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

    written = write_or_exit(target, tables)
    count = sum(len(table.data_rows) for table in tables)
    console_info(f"Exported {count} holding(s) to {written}")


def _badge(valuation: FolioValuation, *, offline: bool) -> str:
    """Disclose how old both caches behind the dashboard are."""
    return cache_badge(
        valuation.quotes,
        computed_at=valuation.ledger.computed_at,
        offline=offline,
    )


def _note(valuation: FolioValuation) -> str:
    """Say, as plain text for a sheet's notes, how old both caches are."""
    return freshness_note(valuation.ledger.computed_at, valuation.quotes)


def show_types(
    valuation: FolioValuation,
    *,
    sort: str | None = None,
    reverse: bool = False,
    wide: bool = False,
    show_closed: bool = False,
    export: str | None = None,
    offline: bool = False,
) -> None:
    """Tile one panel per account type, or export them.

    Args:
        valuation: The folio priced once, which every panel reads its rollups from.
        sort: Order by this column instead of by market value.
        reverse: Flip the sort's natural direction.
        wide: Show the wide-only columns.
        show_closed: Break the aggregate "Closed" row open into one row per
            closed holding.
        export: Write the valued holdings to this path instead of printing.
        offline: Whether the network was kept out of it, which is said.

    Raises:
        typer.Exit: If the export path names a format that cannot be written.
    """
    panels = valuation.panels(
        [type_view(name) for name in valuation.account_types()],
        sort=sort,
        reverse=reverse,
    )
    if not panels:
        console_warning("No open holdings in any account type.")
        return
    if export:
        _export(panels, export, _note(valuation))
        return
    show_by_type(
        [(panel.holdings, panel.flows, panel.view.label) for panel in panels],
        wide=wide,
        show_closed=show_closed,
        badge=_badge(valuation, offline=offline),
    )


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
    """Show the folio dashboard.

    Args:
        account_type: Report the pooled figures for one account type.
        account: Report a single broker account instead.
        currency: `CAD`, `USD` or `native`.
        export: Write the valued holdings to this path instead of printing.
        sort: Order by this column instead of by market value.
        by_type: Tile one panel per account type.
        wide: Show the wide-only columns.
        show_closed: Break the aggregate "Closed" row open into one row per
            closed holding, instead of one summed line.
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

    # Quotes are today's, so the rate that converts them has to be too.
    ledger = require_ledger(refresh=refresh, priced=True, offline=offline)
    if ledger.frame.empty:
        console_warning("No transactions to build a dashboard from.")
        return

    # One valuation for the whole invocation: every panel below reads its
    # rollups from here, and the quotes are fetched once rather than per panel.
    valuation = FolioValuation.build(
        ledger,
        currency=shown,
        refresh=refresh,
        offline=offline,
    )
    if by_type:
        show_types(
            valuation,
            sort=sort,
            reverse=reverse,
            wide=wide,
            show_closed=show_closed,
            export=export,
            offline=offline,
        )
        return

    panel = valuation.panel(
        resolve_pool(account, account_type),
        sort=sort,
        reverse=reverse,
    )

    if export:
        _export([panel], export, _note(valuation))
        return

    show_dashboard(
        panel.holdings,
        panel.flows,
        panel.view.label,
        wide=wide,
        show_closed=show_closed,
        badge=_badge(valuation, offline=offline),
    )
