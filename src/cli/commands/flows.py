"""The `folio flows` command: one pool's flows by week, month or year."""

from __future__ import annotations

from pathlib import Path

import typer

from cli.commands.common import require_ledger, resolve_pool, write_or_exit
from domain import Period
from engine.flows import flow_totals, flows_by_period
from exporters.sheets import period_flows_sheet
from term import console_error, console_info
from ui.views.flows import show_flows

_PERIODS = ", ".join(str(period) for period in Period)


def run_flows(
    period: str = str(Period.MONTH),
    *,
    account_type: str | None = None,
    account: str | None = None,
    export: str | None = None,
) -> None:
    """Show one pool's flows, a row per period and currency.

    Args:
        period: `week`, `month` or `year`.
        account_type: Report one account type's pool.
        account: Report one broker account.
        export: Write the rows to this path instead of printing them.

    Raises:
        typer.Exit: On an unusable request.
    """
    try:
        length = Period(period.strip().lower())
    except ValueError as error:
        console_error(f"Unknown period {period!r}: use {_PERIODS}.")
        raise typer.Exit(1) from error
    pool = resolve_pool(account, account_type)
    ledger = require_ledger()
    periods = flows_by_period(ledger, pool, length)
    if not periods:
        console_info(f"{pool.label} has no flows to show.")
        return
    totals = flow_totals(ledger, pool)
    if export:
        sheet = period_flows_sheet(pool, length, periods, totals)
        written = write_or_exit(Path(export), [sheet])
        console_info(f"Exported {len(sheet.main_table.data_rows)} row(s) to {written}")
        return
    show_flows(pool, length, periods, totals)
