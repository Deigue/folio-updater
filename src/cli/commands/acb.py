"""ACB command for the folio CLI.

Prints an adjustedcostbase style buildup for one symbol, at whichever pool
grain was asked for. All three grains come out of a single replay, so switching
between them costs nothing beyond re-rendering.
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
from domain import CurrencyColumns
from engine.acb_report import AcbReport, AcbShape, NoUsdFiguresError
from engine.fx_rates import load_fx_rates
from engine.gain_since_buy import bought_symbols
from exporters.output import PARQUET_SUFFIX, write_frame_parquet
from exporters.sheets import acb_buildup_sheet, acb_summary_sheet, ledger_sheet
from services.quote_service import QuoteService
from services.symbols import load_symbol_resolver
from term import console_error, console_info, console_warning
from ui.format import (
    cache_badge,
    format_freshness,
    freshness_badge,
    freshness_note,
)
from ui.views.acb import show_buildup, show_summary

if TYPE_CHECKING:
    from exporters.sheet_spec import Sheet
    from models import Quote

_CURRENCY_CHOICES = (CurrencyColumns.BOTH, CurrencyColumns.CAD, CurrencyColumns.USD)


def _sheet(report: AcbReport, *, badge: str) -> Sheet:
    """Build the shape the other flags asked for.

    The same three shapes the command prints: a summary, one symbol's buildup,
    or the whole ledger when neither was asked for.
    """
    label = report.pool.label
    match report.shape:
        case AcbShape.SUMMARY:
            return acb_summary_sheet(report, name=f"ACB - {label}", notes=[badge])
        case AcbShape.BUILDUP:
            return acb_buildup_sheet(
                report,
                name=f"ACB {report.symbol}",
                notes=[badge],
            )
        case AcbShape.LEDGER:
            return ledger_sheet(report, name=f"Ledger - {label}", notes=[badge])


def _export(report: AcbReport, path: str, *, parquet: bool, badge: str) -> None:
    """Write the report out, choosing the format from the suffix.

    Parquet is the report's data at full precision, for a reader that is another
    program. Everything else is the sheet the command reports.

    Args:
        report: The report to write.
        path: Where to write. A path with no suffix becomes a workbook.
        parquet: Whether the path names a Parquet file.
        badge: The freshness line, as plain text for the sheet's notes.

    Raises:
        typer.Exit: If the path names a format that cannot be written.
    """
    target = Path(path)
    if parquet:
        write_frame_parquet(target, report.data)
        console_info(f"Exported {len(report.data)} row(s) to {target}")
        return

    sheet = _sheet(report, badge=badge)
    written = write_or_exit(target, [sheet])
    console_info(f"Exported {len(sheet.main_table.rows)} row(s) to {written}")


def show_acb(  # noqa: PLR0917
    symbol: str | None = None,
    account_type: str | None = None,
    account: str | None = None,
    currency: str = "both",
    date_from: str | None = None,
    date_to: str | None = None,
    year: int | None = None,
    export: str | None = None,
    *,
    show_all: bool = False,
    summary: bool = False,
    refresh: bool = False,
    offline: bool = False,
) -> None:
    """Show the adjusted cost base buildup.

    Args:
        symbol: The security to report on. Required unless `summary` or
            `export` asked for every symbol at once.
        account_type: Report the pooled figures for one account type.
        account: Report a single broker account instead.
        currency: `CAD`, `USD`, or `both`.
        date_from: Only rows traded on or after this date.
        date_to: Only rows traded on or before this date.
        year: Shorthand for a whole calendar year.
        export: Write the report to this path instead of printing it.
        show_all: Include DIVIDEND and FCH rows.
        summary: Print one row per symbol instead of a buildup.
        refresh: Refetch the quotes and replay the cost base before reporting.
        offline: Never touch the network: price from cached quotes only.

    Raises:
        typer.Exit: On an unusable request.
    """
    bootstrap.reload_config()

    if symbol is None and not (summary or export):
        console_error(
            "A SYMBOL is required unless you asked for --summary or --export.",
        )
        raise typer.Exit(1)

    # Quotes are today's, so the rate that converts them has to be too. A summary
    # has no per-row Gain, so it prices nothing.
    ledger = require_ledger(refresh=refresh, priced=not summary, offline=offline)
    if ledger.frame.empty:
        console_warning("No transactions to compute a cost base from.")
        return

    pool = resolve_pool(account, account_type, default_type="nreg")
    shape = (
        AcbShape.SUMMARY if summary else AcbShape.BUILDUP if symbol else AcbShape.LEDGER
    )
    canonical = load_symbol_resolver().canonical(symbol) if symbol else None
    if year is not None:
        date_from, date_to = f"{year}-01-01", f"{year}-12-31"
    # A file for another program carries every currency, so it never refuses one.
    parquet = bool(export) and Path(export).suffix.lower() == PARQUET_SUFFIX
    choice = (
        CurrencyColumns.BOTH
        if parquet
        else parse_currency_choice(currency, _CURRENCY_CHOICES)
    )

    try:
        report = AcbReport.build(
            ledger.frame,
            pool,
            shape,
            symbol=canonical,
            dates=(date_from, date_to),
            currencies=choice,
            income=show_all,
        )
    except NoUsdFiguresError as error:
        console_error(f"{error} Drop --currency USD.")
        raise typer.Exit(1) from error

    quotes: dict[str, Quote] = {}
    if not summary:
        quotes = QuoteService.get_quotes(
            bought_symbols(report.rows),
            refresh=refresh,
            offline=offline,
        )
        report = report.priced(quotes, load_fx_rates())

    if export:
        _export(
            report,
            export,
            parquet=parquet,
            badge=(
                freshness_note(ledger.computed_at, quotes)
                if quotes
                else f"Cost base {format_freshness(ledger.computed_at)}."
            ),
        )
    elif summary:
        show_summary(report, freshness_badge(ledger.computed_at))
    elif report.rows.empty:
        console_warning(f"No {canonical} transactions in the {pool.label} pool.")
    else:
        badge = (
            cache_badge(quotes, computed_at=ledger.computed_at, offline=offline)
            if quotes
            else freshness_badge(ledger.computed_at)
        )
        show_buildup(report, badge)
