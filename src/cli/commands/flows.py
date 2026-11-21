"""The `folio flows` command: one pool's flows by week, month or year."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer

from cli.commands.common import require_ledger, resolve_pool, write_or_exit
from domain import Grain, Period
from engine.accounts import Pool
from engine.flows import flow_totals, flows_by_period, pools_by_period
from exporters.sheets import period_flows_sheet, type_flows_sheet
from term import console_error, console_info
from ui.views.flows import show_flows, show_flows_by_type

if TYPE_CHECKING:
    from exporters.sheet_spec import Sheet

_PERIODS = ", ".join(str(period) for period in Period)


def run_flows(
    period: str = str(Period.MONTH),
    *,
    account_type: str | None = None,
    account: str | None = None,
    export: str | None = None,
    by_type: bool = False,
) -> None:
    """Show one pool's flows, a row per period and currency.

    Args:
        period: `week`, `month` or `year`.
        account_type: Report one account type's pool.
        account: Report one broker account.
        export: Write the rows to this path instead of printing them.
        by_type: Show every account type, side by side within each period.

    Raises:
        typer.Exit: On an unusable request.
    """
    try:
        length = Period(period.strip().lower())
    except ValueError as error:
        console_error(f"Unknown period {period!r}: use {_PERIODS}.")
        raise typer.Exit(1) from error
    if by_type and (account or account_type):
        console_error("--by-type shows every type, so it takes no --type or --account.")
        raise typer.Exit(1)
    if by_type:
        _show_types(length, export)
        return
    pool = resolve_pool(account, account_type)
    ledger = require_ledger()
    periods = flows_by_period(ledger, pool, length)
    if not periods:
        console_info(f"{pool.label} has no flows to show.")
        return
    totals = flow_totals(ledger, pool)
    if export:
        _export(period_flows_sheet(pool, length, periods, totals), export)
        return
    show_flows(pool, length, periods, totals)


def _show_types(period: Period, export: str | None) -> None:
    """Show every account type's flows, each period grouping the types it touched."""
    ledger = require_ledger()
    pools = [Pool(Grain.TYPE, name) for name in ledger.account_types]
    periods = pools_by_period(ledger, pools, period)
    if not periods:
        console_info("No account type has flows to show.")
        return
    totals = [flow_totals(ledger, pool) for pool in pools]
    if export:
        _export(type_flows_sheet(period, periods, totals), export)
        return
    show_flows_by_type(period, periods, totals)


def _export(sheet: Sheet, path: str) -> None:
    """Write the sheet out, choosing the format from the suffix."""
    written = write_or_exit(Path(path), [sheet])
    console_info(f"Exported {len(sheet.main_table.data_rows)} row(s) to {written}")
