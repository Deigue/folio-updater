"""Flows by period, for one pool or every account type, as a sheet."""

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
    from collections.abc import Iterable, Sequence

    from domain import Period
    from engine.accounts import Pool
    from engine.flows import Flows, PeriodFlows, PeriodPools
    from exporters.sheet_spec import Row

_PERIOD = "Period"
_POOL = "Pool"
_CURRENCY = "$"
_TOTAL = "Total"

_COLUMNS: tuple[Col, ...] = (
    Col(_PERIOD, width=10),
    Col(_CURRENCY, width=6),
    *FLOW_FIGURES,
)
_TYPE_COLUMNS: tuple[Col, ...] = (
    Col(_PERIOD, width=10),
    Col(_POOL, width=16),
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
    return _flows_sheet(
        f"{pool.label} by {period}",
        _COLUMNS,
        [(period.label(entry.start), (entry.flows,)) for entry in periods],
        (totals,),
    )


def type_flows_sheet(
    period: Period,
    periods: Sequence[PeriodPools],
    totals: Sequence[Flows],
) -> Sheet:
    """Lay every account type's flows out a row per period, type and currency.

    Args:
        period: How long each period is.
        periods: Every period with a flow, newest first.
        totals: Each account type's flows over its whole history.

    Returns:
        The sheet, each type's totals last.
    """
    return _flows_sheet(
        f"Types by {period}",
        _TYPE_COLUMNS,
        [(period.label(entry.start), entry.pools) for entry in periods],
        totals,
    )


def _flows_sheet(
    name: str,
    columns: tuple[Col, ...],
    periods: Sequence[tuple[str, Sequence[Flows]]],
    totals: Sequence[Flows],
) -> Sheet:
    """Lay each labelled period's pools out, then a spacer and the totals."""
    rows = [row for label, members in periods for row in _rows(columns, label, members)]
    rows.append(blank_row(columns))
    rows.extend(_rows(columns, _TOTAL, totals, Role.TOTAL))
    return Sheet(
        name=name,
        tables=(SheetTable(columns, tuple(rows)),),
        # Every column before the figures, so a row still says what it is.
        freeze=len(columns) - len(FLOW_FIGURES),
    )


def _rows(
    columns: Sequence[Col],
    label: str,
    members: Iterable[Flows],
    role: Role = Role.DATA,
) -> list[Row]:
    """Each pool's lines, one per currency, every row naming its period and pool.

    A sheet is filtered and sorted, so unlike the terminal it repeats the labels a
    row would otherwise borrow from the one above.
    """
    return [
        row_of(
            columns,
            {
                _PERIOD: label,
                _POOL: flows.pool.label,
                _CURRENCY: str(line.currency),
                **read_cells(FLOW_FIGURES, line),
            },
            role,
        )
        for flows in members
        for line in flows.lines
    ]
