"""The `folio flows` view: one pool's flows, a row per period and currency."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.table import Table as RichTable

from domain import Fmt
from exporters.catalog import FLOW_FIGURES
from term import console_print
from ui.cells import currency_tint, figure_cell, style
from ui.layout.columns import FormattedColumn, add_column, formatted
from ui.layout.fit import fit
from ui.layout.paging import page_frame, page_title
from ui.vocabulary import (
    FLOW_COLORS,
    GRAND_TOTAL_ROW_STYLE,
    TABLE_BORDER,
    TABLE_HEADER,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from domain import Period
    from engine.accounts import Pool
    from engine.flows import FlowLine, Flows, PeriodFlows

_PERIOD = "Period"
_TOTAL = "Total"

# Columns given up last of all, least important first: transfers and withdrawals
# are rare, so most pages already leave them out as empty.
_DROP_ORDER = ("Transferred", "Withdrawn", "Fees", "Realized", "Trades")

# The rows below the table: the rule above the totals, and the line saying what
# fitting hid.
_TOTALS_RULE = 1
_HIDDEN_NOTE = 1


def show_flows(
    pool: Pool,
    period: Period,
    periods: Sequence[PeriodFlows],
    totals: Flows,
) -> None:
    """Page through one pool's flows, newest period first, totals under every page.

    Args:
        pool: The pool shown.
        period: How long each period is.
        periods: The pool's periods, newest first.
        totals: The pool's flows over its whole history.
    """
    title = f"{pool.label} flows by {period}"
    rows = [
        (period.label(entry.start), line)
        for entry in periods
        for line in entry.flows.lines
    ]
    total_lines = totals.lines

    def render(start: int, end: int) -> None:
        table = _flows_table(
            rows[start:end],
            total_lines,
            page_title(title, start, end, len(rows)),
        )
        fitted = fit(table, _DROP_ORDER)
        console_print(fitted.table)
        if fitted.note:
            console_print(fitted.note)

    page_frame(
        len(rows),
        title,
        None,
        render,
        reserved=len(total_lines) + _TOTALS_RULE + _HIDDEN_NOTE,
    )


def _flows_table(
    rows: Sequence[tuple[str, FlowLine]],
    total_lines: Sequence[FlowLine],
    title: str | None,
) -> RichTable:
    """Build one page: its period rows, then the whole history's totals."""
    table = RichTable(
        title=title,
        show_header=True,
        header_style=TABLE_HEADER,
        border_style=TABLE_BORDER,
    )
    add_column(table, FormattedColumn(_PERIOD, no_wrap=True))
    # Fitting folds a currency every row shares into the first figure's heading.
    add_column(table, FormattedColumn("$", fmt=Fmt.CURRENCY, no_wrap=True))
    for column in FLOW_FIGURES:
        add_column(table, formatted(column))

    previous = None
    for label, line in rows:
        # A period's further currencies leave its label to the line above.
        shown = label if label != previous else ""
        table.add_row(shown, *_line_cells(line))
        previous = label
    table.add_section()
    for index, line in enumerate(total_lines):
        table.add_row(
            _TOTAL if index == 0 else "",
            *_line_cells(line),
            style=GRAND_TOTAL_ROW_STYLE,
        )
    return table


def _line_cells(line: FlowLine) -> list[str]:
    """Render one currency's line: its currency, then each flow, blank when none."""
    currency = currency_tint(line.currency, str(line.currency))
    figures = [
        style(figure_cell(column, column.read(line)), FLOW_COLORS.get(column.header))
        for column in FLOW_FIGURES
    ]
    return [currency, *figures]
