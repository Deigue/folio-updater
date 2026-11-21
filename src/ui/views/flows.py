"""The `folio flows` view: a row per period, by pool if asked, each currency apart."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.table import Table as RichTable

from domain import AccountType
from engine.accounts import account_type_of
from exporters.catalog import FLOWS_BY_CURRENCY
from term import console_print
from ui.cells import account_type_cell, figure_cell, style
from ui.layout.columns import FormattedColumn, add_column, formatted
from ui.layout.fit import fit
from ui.layout.paging import available_height, group_breaks, page_frame, page_title
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
    from engine.flows import Flows, PeriodFlows, PeriodPools

_PERIOD = "Period"
_POOL = "Pool"
_TOTAL = "Total"

# Columns given up last of all, least important first: transfers and withdrawals
# are rare, so most pages already leave them out as empty.
_DROPPED_FIRST = ("Transferred", "Withdrawn", "Fees", "Realized", "Trades")
_DROP_ORDER = tuple(
    column.header
    for name in _DROPPED_FIRST
    for column in FLOWS_BY_CURRENCY
    if column.full_label == name
)

# The lines a page gives up beyond a one-line header: the heading's second line
# (the currency), the rule above the totals and the line saying what fitting hid.
_CURRENCY_HEADING = 1
_TOTALS_RULE = 1
_HIDDEN_NOTE = 1


@dataclass(frozen=True)
class _Row:
    """One line of the table: a pool's flows within one period."""

    period: str
    flows: Flows
    opens_period: bool


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
    rows = _rows([(period.label(entry.start), (entry.flows,)) for entry in periods])
    _page_through(f"{pool.label} flows by {period}", rows, (totals,), by_pool=False)


def show_flows_by_type(
    period: Period,
    periods: Sequence[PeriodPools],
    totals: Sequence[Flows],
) -> None:
    """Page through every account type's flows, each period grouping its pools.

    Args:
        period: How long each period is.
        periods: Every period with a flow, newest first.
        totals: Each account type's flows over its whole history.
    """
    rows = _rows([(period.label(entry.start), entry.pools) for entry in periods])
    _page_through(f"Flows by type and {period}", rows, totals, by_pool=True)


def _rows(periods: Sequence[tuple[str, Sequence[Flows]]]) -> list[_Row]:
    """Spread each labelled period into a line per pool."""
    return [
        _Row(label, flows, opens_period=not index)
        for label, members in periods
        for index, flows in enumerate(members)
    ]


def _page_through(
    title: str,
    rows: Sequence[_Row],
    totals: Sequence[Flows],
    *,
    by_pool: bool,
) -> None:
    """Page the lines, totals under every page; grouped pools keep a period whole."""
    reserved = len(totals) + _CURRENCY_HEADING + _TOTALS_RULE + _HIDDEN_NOTE
    breaks: list[int] = []
    if by_pool:
        opens = [index for index, row in enumerate(rows) if row.opens_period]
        # A rule above each period costs it a line.
        ends = [*opens[1:], len(rows)]
        heights = [end - start + 1 for start, end in zip(opens, ends, strict=True)]
        room = available_height(table=True, pages=True, reserved=reserved)
        breaks = [opens[group] for group in group_breaks(heights, room)]

    def render(start: int, end: int) -> None:
        table = _flows_table(
            rows[start:end],
            totals,
            page_title(title, start, end, len(rows)),
            by_pool=by_pool,
        )
        fitted = fit(table, _DROP_ORDER)
        console_print(fitted.table)
        if fitted.note:
            console_print(fitted.note)

    page_frame(len(rows), title, None, render, reserved=reserved, breaks=breaks)


def _flows_table(
    rows: Sequence[_Row],
    totals: Sequence[Flows],
    title: str | None,
    *,
    by_pool: bool,
) -> RichTable:
    """Build one page: its lines, then the whole history's totals."""
    table = RichTable(
        title=title,
        show_header=True,
        header_style=TABLE_HEADER,
        border_style=TABLE_BORDER,
    )
    add_column(table, FormattedColumn(_PERIOD, no_wrap=True))
    if by_pool:
        add_column(table, FormattedColumn(_POOL, no_wrap=True))
    for column in FLOWS_BY_CURRENCY:
        add_column(table, formatted(column))

    for index, row in enumerate(rows):
        if by_pool and row.opens_period and index:
            table.add_section()
        # A page that starts partway into a period names it again.
        label = row.period if row.opens_period or not index else ""
        table.add_row(
            label,
            *_pool_cell(row.flows, by_pool=by_pool),
            *_figures(row.flows),
        )
    table.add_section()
    for index, flows in enumerate(totals):
        table.add_row(
            "" if index else _TOTAL,
            *_pool_cell(flows, by_pool=by_pool),
            *_figures(flows),
            style=GRAND_TOTAL_ROW_STYLE,
        )
    return table


def _pool_cell(flows: Flows, *, by_pool: bool) -> list[str]:
    """Name the row's account type, when pools are grouped."""
    if not by_pool:
        return []
    return [account_type_cell(account_type_of(flows.pool) or AccountType.UNKNOWN)]


def _figures(flows: Flows) -> list[str]:
    """Render each flow, each currency in its own column, blank when none."""
    return [
        style(
            figure_cell(column, column.read(flows)),
            FLOW_COLORS.get(column.full_label),
        )
        for column in FLOWS_BY_CURRENCY
    ]
