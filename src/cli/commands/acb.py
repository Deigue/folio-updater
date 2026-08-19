"""ACB command for the folio CLI.

Prints an adjustedcostbase style buildup for one symbol, at whichever pool
grain was asked for. All three grains come out of a single replay, so switching
between them costs nothing beyond re-rendering.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import pandas as pd
import typer
from rich.table import Table as RichTable

from app import bootstrap
from cli import (
    console_error,
    console_info,
    console_print,
    console_warning,
    show_data_table,
)
from cli.commands.common import ensure_fx_coverage
from cli.console import supports_unicode
from cli.display import TRANSACTION_COLORS, fit_table, freshness_badge, page_frame
from engine.cache import load_or_build
from engine.frames import acb_summary_frame, scope_column
from services.symbols import load_symbol_resolver
from utils.constants import (
    ACCOUNT_TYPE_ALIASES,
    AccountType,
    Action,
    Column,
    Currency,
    Scope,
    WarningCode,
)

if TYPE_CHECKING:
    from collections.abc import Container, Hashable
    from typing import Any

    from engine.cache import CachedFrame

# Income rows are hidden unless `--all` asks for them: a dividend never touches
# the cost base, so it is noise in a buildup.
INCOME_IMPACT = "INCOME"

# Column families the buildup renders, as (header, master-frame measure).
_MEASURE_COLUMNS: list[tuple[str, str]] = [
    ("Delta", "Delta"),
    ("ACB", "ACB"),
    ("Avg", "Avg"),
    ("Gain", "Gain"),
]

_BUILDUP_DROP_ORDER = (
    "TxnId",
    "Delta",
    "Delta\nUSD",
    "Rate",
    "Gain\nUSD",
    "ACB\nUSD",
    "Avg\nUSD",
    "Gain",
    "Proceeds",
    "Flags",
    "Fee",
)

_SUMMARY_DROP_ORDER = ("Gain\nUSD", "ACB\nUSD", "Avg\nUSD")

# Running measures whose direction of travel is worth marking, compared against
# the chronologically preceding row.
_MOVING_MEASURES = ("Units", "Avg", "Avg_USD")

_RISE, _FALL = ("▲", "▼") if supports_unicode() else ("^", "v")
_SUPERFICIAL_LOSS_BG = "#4a1414"
_SUPERFICIAL_LOSS_GLYPH = " ⚠" if supports_unicode() else " !"

_MONEY_PRECISION = 2
_AVG_PRECISION = 4
_UNIT_PRECISION = 6
_RATE_PRECISION = 4


class AcbView(NamedTuple):
    """One resolved request for a cost-base buildup.

    Attributes:
        scope: The pool grain to report at.
        pool: The account name or account type the rows are filtered to. Empty
            at portfolio grain, where nothing is filtered out.
        label: How the scope reads in the table title.
    """

    scope: Scope
    pool: str
    label: str


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


def _holding_currency(rows: pd.DataFrame) -> Currency:
    """Report the currency a symbol trades in, taken from its own rows."""
    currencies = set(rows[str(Column.Txn.CURRENCY)].dropna())
    return Currency.USD if str(Currency.USD) in currencies else Currency.CAD


def _resolve_currencies(
    requested: str,
    holding: Currency,
) -> tuple[bool, bool]:
    """Decide which currency variants render.

    Args:
        requested: The `--currency` argument: `CAD`, `USD` or `both`.
        holding: The currency traded in. For a buildup that is the symbol's own
            currency; for a summary it is USD when *any* symbol in the pool is
            USD, since one such holding is enough to earn the columns.

    Returns:
        Whether to show the CAD columns and whether to show the USD ones.

    Raises:
        typer.Exit: If USD columns were demanded where nothing is USD-
            denominated, which would render an empty table rather than say
            anything.
    """
    choice = requested.strip().upper()
    if choice == "USD":
        if holding is not Currency.USD:
            console_error(
                "Nothing here is USD-denominated, so there are no USD figures. "
                "Drop --currency USD.",
            )
            raise typer.Exit(1)
        return False, True
    if choice == "CAD":
        return True, False
    # `both` shows both variants where something is USD. Where nothing is, the
    # `_USD` columns are blank by design and are pure noise.
    return True, holding is Currency.USD


def _format(value: float | None, precision: int, *, blank_zero: bool = False) -> str:
    """Render one numeric cell, leaving a genuine blank blank.

    Args:
        value: The number, or None for a column this row does not populate.
        precision: Decimal places.
        blank_zero: Render an exact zero as empty.

    Returns:
        The formatted cell, or an empty string.
    """
    if value is None or pd.isna(value):
        return ""
    number = float(value) + 0.0  # collapses -0.0, which reads as a real debit
    if blank_zero and number == 0:
        return ""
    return f"{number:,.{precision}f}"


def _units(value: float | None, *, blank_zero: bool = False) -> str:
    """Render a share count, dropping the zeros a whole position does not need."""
    text = _format(value, _UNIT_PRECISION, blank_zero=blank_zero)
    return text.rstrip("0").rstrip(".") if "." in text else text


def _signed(text: str, value: float | None) -> str:
    """Colour a rendered cell by the sign of the number behind it."""
    if not text or value is None or pd.isna(value) or float(value) == 0:
        return text
    colour = "green" if float(value) > 0 else "red"
    return f"[{colour}]{text}[/{colour}]"


def movements(rows: pd.DataFrame, view: AcbView) -> dict[tuple[str, int], int]:
    """Record which way each running measure moved, row by row.

    Compared against the *chronologically* preceding row, so the result still
    reads correctly once the table is flipped into newest-first order. The two
    currency variants of the average are tracked apart: a rate move can push the
    CAD average one way while the USD average goes the other.

    Args:
        rows: The view's rows, in chronological order.
        view: The pool grain being reported.

    Returns:
        `(measure, TxnId)` to `+1` where the measure rose and `-1` where it fell.
        A measure that held still is absent, as is the first row of each series,
        which has nothing to compare against.
    """
    moves: dict[tuple[str, int], int] = {}
    for measure in _MOVING_MEASURES:
        column = scope_column(view.scope, measure)
        if column not in rows.columns:
            continue
        previous: float | None = None
        for _, row in rows.iterrows():
            current = row[column]
            if current is None or pd.isna(current):
                continue
            txn_id = int(row[str(Column.Txn.TXN_ID)])
            if previous is not None and float(current) != previous:
                moves[(measure, txn_id)] = 1 if float(current) > previous else -1
            previous = float(current)
    return moves


def _by_direction(text: str, direction: int | None) -> str:
    """Colour a cell by which way its running value moved.

    Green for a rise and red for a fall, matching the sign colouring on `Units`,
    `Delta` and `Gain`: across this table green always means the number went up.
    """
    if not text or not direction:
        return text
    colour = "green" if direction > 0 else "red"
    return f"[{colour}]{text}[/{colour}]"


def _build_table(
    rows: pd.DataFrame,
    view: AcbView,
    title: str | None,
    moves: dict[tuple[str, int], int],
    *,
    show_cad: bool,
    show_usd: bool,
) -> RichTable:
    """Render the buildup as a Rich table, fitted to the terminal."""
    table = RichTable(
        title=title,
        show_header=True,
        header_style="bold bright_white",
        border_style="bright_blue",
    )
    for name in ("Settle", "TxnId", "Action"):
        table.add_column(name, no_wrap=True)
    for name in ("Units", "Held", "Price", "Amount", "Fee"):
        table.add_column(name, justify="right")
    for header, _measure in _MEASURE_COLUMNS:
        if show_cad:
            table.add_column(header, justify="right")
        if show_usd:
            table.add_column(f"{header}\nUSD", justify="right")
    if show_cad:
        table.add_column("Proceeds", justify="right")
    if show_usd:
        table.add_column("Rate", justify="right")
    table.add_column("Flags")

    for _, row in rows.iterrows():
        table.add_row(
            *_render_row(row, view, moves, show_cad=show_cad, show_usd=show_usd),
        )
    return fit_table(table, _BUILDUP_DROP_ORDER)


def _measure_cell(
    row: pd.Series,
    column: str,
    measure: str,
    moves: dict[tuple[str, int], int],
    *,
    superficial_loss: bool = False,
) -> str:
    """Render one per-scope measure, coloured or annotated as that measure wants."""
    precision = _AVG_PRECISION if measure.startswith("Avg") else _MONEY_PRECISION
    value = row[column]
    # A zero delta or gain says nothing: blank it to zero.
    # A zero ACB or average is a real, closed position.
    text = _format(value, precision, blank_zero=measure.startswith(("Delta", "Gain")))
    if measure.startswith(("Delta", "Gain")):
        cell = _signed(text, value)
        if measure.startswith("Gain") and superficial_loss and cell:
            cell += _SUPERFICIAL_LOSS_GLYPH
            return f"[on {_SUPERFICIAL_LOSS_BG}]{cell}[/on {_SUPERFICIAL_LOSS_BG}]"
        return cell
    if measure.startswith("Avg") and text:
        direction = moves.get((measure, int(row[str(Column.Txn.TXN_ID)])))
        return f"{_arrow(direction)}{text}"
    return text


def _arrow(direction: int | None) -> str:
    """Render the direction marker for an average-cost move."""
    if not direction:
        return ""
    if direction > 0:
        return f"[green]{_RISE}[/green]"
    return f"[red]{_FALL}[/red]"


def _render_row(
    row: pd.Series,
    view: AcbView,
    moves: dict[tuple[str, int], int],
    *,
    show_cad: bool,
    show_usd: bool,
) -> list[str]:
    """Render one buildup row into display strings."""
    action = str(row[str(Column.Txn.ACTION)])
    colour = TRANSACTION_COLORS.get(Action(action), "white") if action else "white"
    units = row[str(Column.Txn.UNITS)]
    superficial_loss = str(WarningCode.SUPERFICIAL_LOSS_SUSPECT) in str(
        row["Flags"] or "",
    ).split(",")
    cells = [
        str(row[str(Column.Txn.SETTLE_DATE)]),
        str(row[str(Column.Txn.TXN_ID)]),
        f"[{colour}]{action}[/{colour}]",
        _signed(_units(units, blank_zero=True), units),
        _by_direction(
            _units(row[scope_column(view.scope, "Units")]),
            moves.get(("Units", int(row[str(Column.Txn.TXN_ID)]))),
        ),
        _format(row[str(Column.Txn.PRICE)], _MONEY_PRECISION, blank_zero=True),
        _format(row[str(Column.Txn.AMOUNT)], _MONEY_PRECISION, blank_zero=True),
        _format(row[str(Column.Txn.FEE)], _MONEY_PRECISION, blank_zero=True),
    ]
    for _header, measure in _MEASURE_COLUMNS:
        if show_cad:
            cells.append(
                _measure_cell(
                    row,
                    scope_column(view.scope, measure),
                    measure,
                    moves,
                    superficial_loss=superficial_loss,
                ),
            )
        if show_usd:
            usd = f"{measure}_USD"
            cells.append(
                _measure_cell(
                    row,
                    scope_column(view.scope, usd),
                    usd,
                    moves,
                    superficial_loss=superficial_loss,
                ),
            )
    if show_cad:
        cells.append(_format(row["Proceeds"], _MONEY_PRECISION))
    if show_usd:
        cells.append(_format(row["FXRate"], _RATE_PRECISION))
    cells.append(str(row["Flags"] or ""))
    return cells


def _print_footer(
    rows: pd.DataFrame,
    exclude: Container[str] = (),
) -> None:
    """Print the flag roll-up and conversion note.

    Footer line printed once after `page_frame` returns.

    Args:
        rows: The rows whose `Flags` column is rolled up.
        exclude: Flag codes to leave out of the roll-up, e.g. a code that is
            only meaningful per-lot and would just be noise once pooled across
            symbols.
    """
    flags: dict[str, int] = {}
    for value in rows["Flags"]:
        for code in str(value or "").split(","):
            if code and code not in exclude:
                flags[code] = flags.get(code, 0) + 1

    if flags:
        summary = "  ".join(f"{code} x{count}" for code, count in sorted(flags.items()))
        console_warning(f"Flags: {summary}")

    console_print(
        "[dim]CAD cost base is converted at each transaction's settle date, "
        "not at today's rate.[/dim]",
    )


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

    if summary:
        _show_summary(rows, view, currency, cached)
    elif rows.empty:
        console_warning(f"No {canonical} transactions in the {view.label} pool.")
    else:
        _show_buildup(rows, view, canonical or "", currency, cached)

    if export:
        _export(rows, export)


def _summary_currency(summary: pd.DataFrame, view: AcbView) -> Currency:
    """Report whether a summarised pool holds anything USD-denominated.

    A pool is normally mixed, so the question is not "which currency" but
    "are the `_USD` columns worth the width". One USD holding is enough.
    """
    column = scope_column(view.scope, "ACB_USD")
    if column in summary.columns and summary[column].notna().any():
        return Currency.USD
    return Currency.CAD


def _open_positions_first(summary: pd.DataFrame, view: AcbView) -> pd.DataFrame:
    """Sink closed positions to the bottom, alphabetical within each group.

    A closed position is history: its row is a realized gain and four blanks.
    Interleaving those alphabetically pushes what is actually held apart and
    makes the list of current holdings something you have to hunt for.
    """
    units = scope_column(view.scope, "Units")
    ordered = summary.assign(_closed=summary[units].fillna(0) == 0)
    return ordered.sort_values(
        ["_closed", "Symbol"],
        kind="stable",
    ).drop(columns="_closed")


def _drop_cad_only(summary: pd.DataFrame, view: AcbView) -> pd.DataFrame:
    """Keep only USD-denominated holdings, for a USD-only request.

    Asked for USD figures alone, a CAD holding has none to give and renders as
    an entirely blank row. Applied only when the CAD columns are hidden: with
    them showing, a CAD holding still has everything to say.
    """
    return summary[summary[scope_column(view.scope, "ACB_USD")].notna()]


def _summary_record(
    record: dict[Hashable, Any],
    view: AcbView,
    *,
    show_cad: bool,
    show_usd: bool,
) -> dict[str, str]:
    """Render one symbol's closing position for the summary table."""

    def cell(measure: str, precision: int) -> str:
        value = record[scope_column(view.scope, measure)]
        text = _format(value, precision, blank_zero=True)
        return _signed(text, value) if measure.startswith("Gain") else text

    cells = {
        "Symbol": str(record["Symbol"]),
        "Units": _units(record[scope_column(view.scope, "Units")], blank_zero=True),
    }
    for measure, precision in (
        ("ACB", _MONEY_PRECISION),
        ("Avg", _AVG_PRECISION),
        ("Gain", _MONEY_PRECISION),
    ):
        if show_cad:
            cells[measure] = cell(measure, precision)
        if show_usd:
            cells[f"{measure}\nUSD"] = cell(f"{measure}_USD", precision)
    return cells


def _show_summary(
    rows: pd.DataFrame,
    view: AcbView,
    currency: str,
    cached: CachedFrame,
) -> None:
    """Print one row per symbol, all three scopes side by side."""
    summary = acb_summary_frame(rows)
    if summary.empty:
        console_warning(f"No holdings in the {view.label} pool.")
        return

    show_cad, show_usd = _resolve_currencies(
        currency,
        _summary_currency(summary, view),
    )
    if show_usd and not show_cad:
        summary = _drop_cad_only(summary, view)
    records = [
        _summary_record(record, view, show_cad=show_cad, show_usd=show_usd)
        for record in _open_positions_first(summary, view).to_dict("records")
    ]
    console_print(freshness_badge(cached.computed_at))
    show_data_table(
        records,
        title=f"ACB summary - {view.label}",
        max_rows=len(records),
        drop_order=_SUMMARY_DROP_ORDER,
    )
    _print_footer(rows, exclude={str(WarningCode.SUPERFICIAL_LOSS_SUSPECT)})


def _show_buildup(
    rows: pd.DataFrame,
    view: AcbView,
    symbol: str,
    currency: str,
    cached: CachedFrame,
) -> None:
    """Print the per-transaction buildup for one symbol.

    Rows are dated and ordered by *settle* date (the date the cash and the FX
    rate belong to) and shown newest first, the way adjustedcostbase.ca lists
    them. The cost base itself is still replayed in trade-date order; this is
    presentation only, and the two orders disagree on a handful of rows where a
    same-day action settles before a trade made earlier.
    """
    show_cad, show_usd = _resolve_currencies(currency, _holding_currency(rows))
    title = f"ACB - {symbol} ({view.label})"
    chronological = rows.sort_values(
        [str(Column.Txn.SETTLE_DATE), str(Column.Txn.TXN_ID)],
        kind="stable",
    )
    # Movements are computed oldest-first so they survive the flip below.
    moves = movements(chronological, view)
    ordered = chronological.iloc[::-1]

    def render(start: int, end: int) -> None:
        page = ordered.iloc[start:end]
        page_title = title if start == 0 and end == len(ordered) else None
        console_print(
            _build_table(
                page,
                view,
                page_title,
                moves,
                show_cad=show_cad,
                show_usd=show_usd,
            ),
        )

    badge = freshness_badge(cached.computed_at)
    page_frame(
        len(ordered),
        title,
        None,
        render,
        badge=badge,
        # Showing both currencies stacks each measure's header onto a second line
        reserved=1 if show_usd else 0,
    )
    _print_footer(ordered)
