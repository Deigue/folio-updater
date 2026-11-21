"""One pool's flows by period, as a sheet."""

from __future__ import annotations

from typing import TYPE_CHECKING

from exporters.catalog import FLOW_FIGURES
from exporters.sheet_spec import (
    Col,
    Role,
    Sheet,
    SheetTable,
    blank_row,
    read_cells,
    row_of,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from domain import Period
    from engine.accounts import Pool
    from engine.flows import FlowLine, Flows, PeriodFlows
    from exporters.sheet_spec import Row

_PERIOD = "Period"
_CURRENCY = "$"

_COLUMNS: tuple[Col, ...] = (
    Col(_PERIOD, width=10),
    Col(_CURRENCY, width=6),
    *FLOW_FIGURES,
)


def period_flows_sheet(
    pool: Pool,
    period: Period,
    periods: Sequence[PeriodFlows],
    totals: Flows,
) -> Sheet:
    """Lay one pool's flows out a row per period and currency, totals last.

    Args:
        pool: The pool shown.
        period: How long each period is.
        periods: The pool's periods, newest first.
        totals: The pool's flows over its whole history.

    Returns:
        The sheet.
    """
    rows = [
        _row(period.label(entry.start), line)
        for entry in periods
        for line in entry.flows.lines
    ]
    rows.append(blank_row(_COLUMNS))
    rows.extend(_row("Total", line, Role.TOTAL) for line in totals.lines)
    return Sheet(
        name=f"{pool.label} by {period}",
        tables=(SheetTable(_COLUMNS, tuple(rows)),),
        freeze=2,
    )


def _row(label: str, line: FlowLine, role: Role = Role.DATA) -> Row:
    """One currency's line, under the period or total it belongs to."""
    cells = {
        _PERIOD: label,
        _CURRENCY: str(line.currency),
        **read_cells(FLOW_FIGURES, line),
    }
    return row_of(_COLUMNS, cells, role)
