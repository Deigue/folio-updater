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
from cli.commands.common import ensure_fx_coverage, resolve_pool
from domain import Column, Scope
from engine.cache import load_or_build
from exporters.output import SingleSheetError, UnsupportedExportError, write_export
from exporters.sheets import (
    acb_buildup_table,
    acb_ledger_table,
    acb_summary_table,
)
from services.symbols import load_symbol_resolver
from term import console_error, console_info, console_warning
from ui.format import format_freshness
from ui.views.acb import AcbView, NoUsdFiguresError, show_buildup, show_summary

if TYPE_CHECKING:
    import pandas as pd

    from exporters.table import Table

# Income rows are hidden unless `--all` asks for them: a dividend never touches
# the cost base, so it is noise in a buildup.
INCOME_IMPACT = "INCOME"

# The one format that stays a raw dump: it is read by programs, not people.
PARQUET_SUFFIX = ".parquet"


def resolve_view(
    account: str | None,
    account_type: str | None,
) -> AcbView:
    """Decide which pool a request is asking about.

    A bare `folio acb MSFT` reports the non-registered pool, which is where the
    CRA-relevant figures live.

    Args:
        account: A single broker account, when `--account` was given.
        account_type: An account type, or `all` for the portfolio-wide pool,
            when `--type` was given.

    Returns:
        The resolved view.

    Raises:
        typer.Exit: If the requested account type is not one the engine knows.
    """
    scope, pool, label = resolve_pool(
        account,
        account_type,
        default_type="nreg",
    )
    return AcbView(scope, pool, label if scope is Scope.ACCOUNT else label.lower())


def _filter_rows(
    frame: pd.DataFrame,
    view: AcbView,
    symbol: str | None,
    *,
    date_from: str | None,
    date_to: str | None,
    show_all: bool,
) -> pd.DataFrame:
    """Narrow the master frame to the rows a request asked for."""
    rows = frame
    if view.scope is Scope.ACCOUNT:
        rows = rows[rows[str(Column.Txn.ACCOUNT)] == view.pool]
    elif view.scope is Scope.TYPE:
        rows = rows[rows["AcctType"] == view.pool]
    if symbol is not None:
        rows = rows[rows["Symbol"] == symbol]
    if not show_all:
        rows = rows[rows["Impact"] != INCOME_IMPACT]
    if date_from:
        rows = rows[rows[str(Column.Txn.TXN_DATE)] >= date_from]
    if date_to:
        rows = rows[rows[str(Column.Txn.TXN_DATE)] <= date_to]
    return rows


def _sheet(
    rows: pd.DataFrame,
    view: AcbView,
    *,
    symbol: str | None,
    currency: str,
    badge: str,
    summary: bool,
) -> Table:
    """Build the shape the other flags asked for.

    The same three shapes the command prints: a summary, one symbol's buildup,
    or the whole ledger when neither was asked for.
    """
    if summary:
        return acb_summary_table(
            rows,
            scope=view.scope,
            name=f"ACB Summary - {view.label}",
            currency=currency,
            notes=[badge],
        )
    if symbol:
        return acb_buildup_table(
            rows,
            scope=view.scope,
            name=f"ACB {symbol}",
            currency=currency,
            notes=[badge],
        )
    return acb_ledger_table(
        rows,
        name=f"ACB - {view.label}",
        currency=currency,
        notes=[badge],
    )


def _export(
    rows: pd.DataFrame,
    view: AcbView,
    path: str,
    *,
    symbol: str | None,
    currency: str,
    badge: str,
    summary: bool,
) -> None:
    """Write the reported rows out, choosing the format from the suffix.

    Parquet stays the raw frame at full precision, for a reader that is another
    program. Everything else is the table the command reports.

    Args:
        rows: The filtered rows from the master frame.
        view: The pool being reported, which names the sheet.
        path: Where to write. A path with no suffix becomes a workbook.
        symbol: The security asked about, when one was.
        currency: `CAD`, `USD` or `both`.
        badge: The freshness line, as plain text for the sheet's notes.
        summary: Whether one row per symbol was asked for.

    Raises:
        typer.Exit: If the path names a format that cannot be written.
    """
    target = Path(path)
    if target.suffix.lower() == PARQUET_SUFFIX:
        rows.to_parquet(target, engine="fastparquet", index=False)
        console_info(f"Exported {len(rows)} row(s) to {target}")
        return

    table = _sheet(
        rows,
        view,
        symbol=symbol,
        currency=currency,
        badge=badge,
        summary=summary,
    )
    try:
        written = write_export(target, [table])
    except (UnsupportedExportError, SingleSheetError) as error:
        console_error(str(error))
        raise typer.Exit(1) from error
    console_info(f"Exported {len(table.rows)} row(s) to {written}")


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
        export: Write the rendered rows to this path instead of only printing.
        show_all: Include DIVIDEND and FCH rows.
        summary: Print one row per symbol instead of a buildup.
        refresh: Rebuild the cache before reporting.

    Raises:
        typer.Exit: On an unusable request.
    """
    bootstrap.reload_config()

    if symbol is None and not (summary or export):
        console_error(
            "A SYMBOL is required unless you asked for --summary or --export.",
        )
        raise typer.Exit(1)

    ensure_fx_coverage()
    cached = load_or_build(refresh=refresh)
    if cached.frame.empty:
        console_warning("No transactions to compute a cost base from.")
        return

    view: AcbView = resolve_view(account, account_type)
    canonical = load_symbol_resolver().canonical(symbol) if symbol else None
    if year is not None:
        date_from, date_to = f"{year}-01-01", f"{year}-12-31"

    rows = _filter_rows(
        cached.frame,
        view,
        canonical,
        date_from=date_from,
        date_to=date_to,
        show_all=show_all,
    )

    try:
        if summary:
            show_summary(rows, view, currency, cached)
        elif rows.empty:
            console_warning(f"No {canonical} transactions in the {view.label} pool.")
        else:
            show_buildup(rows, view, canonical or "", currency, cached)
    except NoUsdFiguresError as error:
        console_error(str(error))
        raise typer.Exit(1) from error

    if export:
        _export(
            rows,
            view,
            export,
            symbol=canonical,
            currency=currency,
            badge=f"Cost base {format_freshness(cached.computed_at)}.",
            summary=summary,
        )
