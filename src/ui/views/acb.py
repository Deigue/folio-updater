"""Rendering the adjusted cost base, as a buildup and as a summary.

Two shapes over the same `AcbReport`: one row per transaction for a single
symbol, and one row per symbol across a pool. The report has already decided
the rows, their order, the currency columns, the movement arrows and the flag
counts; this only lays them out.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd
from rich.table import Table as RichTable

from domain import Action, Column, DiagnosticCode, Grain
from engine.frames import grain_column
from exporters.sheet_spec import Fmt
from term import console_print, console_warning, supports_unicode
from ui.cells import graded, lifetime_return
from ui.layout.columns import FormattedColumn, add_column
from ui.layout.fit import fit_table
from ui.layout.paging import page_frame, page_title
from ui.vocabulary import (
    GAIN,
    LOSS,
    MONEY_PRECISION,
    PRICE_PRECISION,
    RATE_PRECISION,
    RETURN_STRONG_AT,
    SUPERFICIAL_LOSS_BG,
    TABLE_BORDER,
    TABLE_HEADER,
    TRANSACTION_COLORS,
    UNIT_PRECISION,
)
from ui.widgets import show_data_table

if TYPE_CHECKING:
    from collections.abc import Hashable, Mapping
    from typing import Any

    from engine.acb_report import AcbReport


def _figure(header: str, fmt: Fmt, *, coarse: bool = False) -> FormattedColumn:
    """Describe a right-aligned column of figures."""
    return FormattedColumn(header, fmt=fmt, coarse=coarse, justify="right")


def _in_usd(column: FormattedColumn) -> FormattedColumn:
    """Describe the same column holding its figures in USD."""
    header = f"{column.header}\nUSD"
    return replace(column, header=header, key=header)


# Which transaction a row is.
_IDENTITY = (
    FormattedColumn("Settlement", fmt=Fmt.DATE, no_wrap=True),
    FormattedColumn("TxnId", fmt=Fmt.ID, no_wrap=True),
    FormattedColumn("Action", fmt=Fmt.ACTION, no_wrap=True),
)

# What each transaction moved, in display order.
_MOVEMENTS = (
    _figure("Units", Fmt.UNITS),
    _figure("Held", Fmt.UNITS),
    _figure("Price", Fmt.PRICE),
    _figure("Amount", Fmt.MONEY_SIGNED),
    _figure("Fee", Fmt.MONEY),
)

# Column families the buildup renders, by ledger measure (also the header).
_MEASURE_COLUMNS = (
    _figure("Delta", Fmt.MONEY_SIGNED),
    _figure("ACB", Fmt.MONEY),
    _figure("Avg", Fmt.PRICE),
    _figure("Realized", Fmt.MONEY_SIGNED),
)
_MEASURES = tuple(column.key for column in _MEASURE_COLUMNS)

# The row's own gain and its return.
_GAIN = (
    _figure("Gain", Fmt.MONEY_SIGNED),
    _figure("Gain%", Fmt.PERCENT_SIGNED, coarse=True),
)
_BUILDUP_DROP_ORDER = (
    "TxnId",
    "Delta",
    "Delta\nUSD",
    "Rate",
    "Realized\nUSD",
    "ACB\nUSD",
    "Avg\nUSD",
    "Realized",
    "Proceeds",
    "Flags",
    "Fee",
)

_SUMMARY_DROP_ORDER = ("Realized\nUSD", "ACB\nUSD", "Avg\nUSD")

# The summary's figures: units, then the cost base, average and realized gain in
# either currency. Left-aligned, like every other column `show_data_table` draws.
_SUMMARY_COLUMNS = tuple(
    replace(column, justify="left", no_wrap=True)
    for column in (
        _figure("Units", Fmt.UNITS),
        *_MEASURE_COLUMNS[1:],
        *(_in_usd(column) for column in _MEASURE_COLUMNS[1:]),
    )
)
_RISE, _FALL = ("▲", "▼") if supports_unicode() else ("^", "v")
_SUPERFICIAL_LOSS_GLYPH = " ⚠" if supports_unicode() else " !"


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
    """Render a unit count, dropping the zeros whole units do not need."""
    text = _format(value, UNIT_PRECISION, blank_zero=blank_zero)
    return text.rstrip("0").rstrip(".") if "." in text else text


def _signed(text: str, value: float | None) -> str:
    """Colour a rendered cell by the sign of the number behind it."""
    if not text or value is None or pd.isna(value) or float(value) == 0:
        return text
    colour = GAIN if float(value) > 0 else LOSS
    return f"[{colour}]{text}[/{colour}]"


def _by_direction(text: str, direction: int | None) -> str:
    """Colour a cell by which way its running value moved.

    Green for a rise and red for a fall, matching the sign colouring on `Units`,
    `Delta` and `Realized`: across this table green always means the number went up.
    """
    if not text or not direction:
        return text
    colour = GAIN if direction > 0 else LOSS
    return f"[{colour}]{text}[/{colour}]"


def _build_table(
    rows: pd.DataFrame,
    grain: Grain,
    title: str | None,
    moves: Mapping[tuple[str, int], int],
    *,
    show_cad: bool,
    show_usd: bool,
) -> RichTable:
    """Render the buildup as a Rich table, fitted to the terminal."""
    table = RichTable(
        title=title,
        show_header=True,
        header_style=TABLE_HEADER,
        border_style=TABLE_BORDER,
    )
    columns = [*_IDENTITY, *_MOVEMENTS]
    for column in _MEASURE_COLUMNS:
        if show_cad:
            columns.append(column)
        if show_usd:
            columns.append(_in_usd(column))
    # The row's own gain, in USD where USD is shown and in CAD otherwise, never
    # dropped. One currency only: two more columns is all the width spares.
    columns.extend(_in_usd(column) if show_usd else column for column in _GAIN)
    if show_cad:
        columns.append(_figure("Proceeds", Fmt.MONEY))
    if show_usd:
        columns.append(_figure("Rate", Fmt.RATE))
    columns.append(FormattedColumn("Flags"))
    for column in columns:
        add_column(table, column)

    for _, row in rows.iterrows():
        table.add_row(
            *_render_row(row, grain, moves, show_cad=show_cad, show_usd=show_usd),
        )
    return fit_table(table, _BUILDUP_DROP_ORDER)


def _measure_cell(
    row: pd.Series,
    column: str,
    measure: str,
    moves: Mapping[tuple[str, int], int],
) -> str:
    """Render one per-grain measure, coloured as that measure wants."""
    precision = PRICE_PRECISION if measure.startswith("Avg") else MONEY_PRECISION
    value = row[column]
    # A zero delta or realized gain says nothing: blank it to zero.
    # A zero ACB or average is a real, closed holding.
    text = _format(
        value,
        precision,
        blank_zero=measure.startswith(("Delta", "Realized")),
    )
    if measure.startswith(("Delta", "Realized")):
        return _signed(text, value)
    if measure.startswith("Avg") and text:
        direction = moves.get((measure, int(row[str(Column.Txn.TXN_ID)])))
        return f"{_arrow(direction)}{text}"
    return text


def _gain_cells(
    row: pd.Series,
    grain: Grain,
    suffix: str,
    *,
    superficial_loss: bool,
) -> list[str]:
    """Render the row's own Gain and Gain%, coloured by how large the gain is.

    Args:
        row: The buildup row.
        grain: The pool grain being reported.
        suffix: `_USD` for the USD figures, empty for the CAD ones.
        superficial_loss: Whether the row is flagged as a suspect superficial
            loss, marked on the sale's own gain.

    Returns:
        The Gain cell, then the Gain% cell.
    """
    gain = row[grain_column(grain, f"Gain{suffix}")]
    ratio = row[grain_column(grain, f"GainPct{suffix}")]
    text = _format(gain, MONEY_PRECISION)
    if pd.isna(ratio):
        # Nothing to grade by: a buy with no quote, or a sale that removed no cost.
        money, share = _signed(text, gain), ""
    else:
        graded_by = Decimal(str(ratio))
        money = graded(text, graded_by, RETURN_STRONG_AT, Decimal(str(gain)))
        share = lifetime_return(graded_by)
    if superficial_loss and money:
        money = (
            f"[on {SUPERFICIAL_LOSS_BG}]{money}{_SUPERFICIAL_LOSS_GLYPH}"
            f"[/on {SUPERFICIAL_LOSS_BG}]"
        )
    return [money, share]


def _arrow(direction: int | None) -> str:
    """Render the direction marker for an average-cost move."""
    if not direction:
        return ""
    if direction > 0:
        return f"[{GAIN}]{_RISE}[/{GAIN}]"
    return f"[{LOSS}]{_FALL}[/{LOSS}]"


def _render_row(
    row: pd.Series,
    grain: Grain,
    moves: Mapping[tuple[str, int], int],
    *,
    show_cad: bool,
    show_usd: bool,
) -> list[str]:
    """Render one buildup row into display strings."""
    action = str(row[str(Column.Txn.ACTION)])
    colour = TRANSACTION_COLORS.get(Action(action), "white") if action else "white"
    units = row[str(Column.Txn.UNITS)]
    superficial_loss = str(DiagnosticCode.SUPERFICIAL_LOSS_SUSPECT) in str(
        row["Flags"] or "",
    ).split(",")
    cells = [
        str(row[str(Column.Txn.SETTLE_DATE)]),
        str(row[str(Column.Txn.TXN_ID)]),
        f"[{colour}]{action}[/{colour}]",
        _signed(_units(units, blank_zero=True), units),
        _by_direction(
            _units(row[grain_column(grain, "Units")]),
            moves.get(("Units", int(row[str(Column.Txn.TXN_ID)]))),
        ),
        _format(row[str(Column.Txn.PRICE)], MONEY_PRECISION, blank_zero=True),
        _format(row[str(Column.Txn.AMOUNT)], MONEY_PRECISION, blank_zero=True),
        _format(row[str(Column.Txn.FEE)], MONEY_PRECISION, blank_zero=True),
    ]
    for measure in _MEASURES:
        if show_cad:
            cells.append(
                _measure_cell(row, grain_column(grain, measure), measure, moves),
            )
        if show_usd:
            usd = f"{measure}_USD"
            cells.append(
                _measure_cell(row, grain_column(grain, usd), usd, moves),
            )
    cells.extend(
        _gain_cells(
            row,
            grain,
            "_USD" if show_usd else "",
            superficial_loss=superficial_loss,
        ),
    )
    if show_cad:
        cells.append(_format(row["Proceeds"], MONEY_PRECISION))
    if show_usd:
        cells.append(_format(row["FXRate"], RATE_PRECISION))
    cells.append(str(row["Flags"] or ""))
    return cells


def _print_footer(flag_counts: Mapping[str, int]) -> None:
    """Print the flag roll-up and conversion note.

    Footer line printed once after `page_frame` returns.

    Args:
        flag_counts: How many rows carry each flag, as the report counted them.
    """
    if flag_counts:
        summary = "  ".join(f"{code} x{count}" for code, count in flag_counts.items())
        console_warning(f"Flags: {summary}")

    console_print(
        "[dim]CAD cost base is converted at each transaction's settlement date, "
        "not at today's rate.[/dim]",
    )


def _summary_record(
    record: dict[Hashable, Any],
    grain: Grain,
    *,
    show_cad: bool,
    show_usd: bool,
) -> dict[str, str]:
    """Render one symbol's final figures for the summary table."""

    def cell(measure: str, precision: int) -> str:
        value = record[grain_column(grain, measure)]
        text = _format(value, precision, blank_zero=True)
        return _signed(text, value) if measure.startswith("Realized") else text

    cells = {
        "Symbol": str(record["Symbol"]),
        "Units": _units(record[grain_column(grain, "Units")], blank_zero=True),
    }
    for measure, precision in (
        ("ACB", MONEY_PRECISION),
        ("Avg", PRICE_PRECISION),
        ("Realized", MONEY_PRECISION),
    ):
        if show_cad:
            cells[measure] = cell(measure, precision)
        if show_usd:
            cells[f"{measure}\nUSD"] = cell(f"{measure}_USD", precision)
    return cells


def show_summary(report: AcbReport, badge: str) -> None:
    """Print one row per symbol, all three grains side by side.

    Args:
        report: The pool's cost base summary report.
        badge: The freshness line printed above the table.
    """
    label = report.pool.label
    if report.holdings.empty:
        console_warning(f"No holdings in the {label} pool.")
        return

    show_cad, show_usd = report.currencies
    records = [
        _summary_record(
            record,
            report.pool.grain,
            show_cad=show_cad,
            show_usd=show_usd,
        )
        for record in report.holdings.to_dict("records")
    ]
    console_print(badge)
    show_data_table(
        records,
        title=f"Cost base summary - {label}",
        max_rows=len(records),
        drop_order=_SUMMARY_DROP_ORDER,
        columns=_SUMMARY_COLUMNS,
    )
    _print_footer(report.flag_counts)


def show_buildup(report: AcbReport, badge: str) -> None:
    """Print the per-transaction buildup for one symbol, newest first.

    Rows are dated and ordered by *settlement* date (the date the cash and the FX
    rate belong to) and shown newest first, the way adjustedcostbase.ca lists
    them. The cost base itself is still replayed in transaction-date order; this is
    presentation only, and the two orders disagree on a handful of rows where a
    same-day action settles before a trade made earlier.

    Args:
        report: The symbol's report, priced, its rows oldest first.
        badge: The freshness line printed above the table.
    """
    show_cad, show_usd = report.currencies
    title = f"ACB - {report.symbol} ({report.pool.label})"
    ordered = report.rows.iloc[::-1]

    def render(start: int, end: int) -> None:
        page = ordered.iloc[start:end]
        console_print(
            _build_table(
                page,
                report.pool.grain,
                page_title(title, start, end, len(ordered)),
                report.directions,
                show_cad=show_cad,
                show_usd=show_usd,
            ),
        )

    page_frame(
        len(ordered),
        title,
        None,
        render,
        badge=badge,
        # Showing both currencies stacks each measure's header onto a second line
        reserved=1 if show_usd else 0,
    )
    _print_footer(report.flag_counts)
