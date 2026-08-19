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
from cli.commands.common import ensure_fx_coverage
from engine.cache import load_or_build
from services.symbols import load_symbol_resolver
from ui import console_error, console_info, console_warning
from ui.views.acb import AcbView, NoUsdFiguresError, show_buildup, show_summary
from utils.constants import ACCOUNT_TYPE_ALIASES, AccountType, Column, Scope

if TYPE_CHECKING:
    import pandas as pd

# Income rows are hidden unless `--all` asks for them: a dividend never touches
# the cost base, so it is noise in a buildup.
INCOME_IMPACT = "INCOME"


def _resolve_type(value: str) -> AccountType | None:
    """Read a `--type` argument as an `AccountType`."""
    token = value.strip().upper()
    if token in ACCOUNT_TYPE_ALIASES:
        return ACCOUNT_TYPE_ALIASES[token]
    try:
        return AccountType(token)
    except ValueError:
        return None


def resolve_view(
    account: str | None,
    account_type: str | None,
    *,
    folio: bool,
) -> AcbView:
    """Decide which pool a request is asking about.

    A bare `folio acb MSFT` reports the non-registered pool, which is where the
    CRA-relevant figures live.

    Args:
        account: A single broker account, when `--account` was given.
        account_type: An account type, when `--type` was given.
        folio: Whether `--folio` asked for the portfolio-wide pool.

    Returns:
        The resolved view.

    Raises:
        typer.Exit: If the requested account type is not one the engine knows.
    """
    if folio:
        return AcbView(Scope.FOLIO, "", "portfolio")
    if account:
        return AcbView(Scope.ACCOUNT, account, account)

    resolved = _resolve_type(account_type or "nreg")
    if resolved is None:
        console_error(
            f"Unknown account type '{account_type}'. Try one of: "
            f"{', '.join(str(member).lower() for member in AccountType)}",
        )
        raise typer.Exit(1)
    return AcbView(Scope.TYPE, str(resolved), str(resolved).replace("_", "-").lower())


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


def _export(frame: pd.DataFrame, path: str) -> None:
    """Write the rendered rows out, choosing the format from the suffix."""
    target = Path(path)
    if target.suffix.lower() == ".parquet":
        frame.to_parquet(target, engine="fastparquet", index=False)
    else:
        frame.to_csv(target, index=False)
    console_info(f"Exported {len(frame)} row(s) to {target}")


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
    folio: bool = False,
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
        folio: Report the portfolio-wide pool.
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

    view: AcbView = resolve_view(account, account_type, folio=folio)
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
        _export(rows, export)
