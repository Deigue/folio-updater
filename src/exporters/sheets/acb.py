"""The cost-base sheets: a symbol's buildup, a pool summary, and the Cost Base."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import Column, Scope
from engine.frames import (
    acb_summary_frame,
    acb_summary_frames_by_pool,
    scope_column,
)
from engine.panels import FOLIO_VIEW, type_view
from exporters.excel_style import COST_BASE_TAB
from exporters.sheets.summary import SCOPE_NAMES
from exporters.table import Col, Fmt, Table, row_of

if TYPE_CHECKING:
    from collections.abc import Hashable, Mapping, Sequence
    from typing import Any

    import pandas as pd

USD = "_USD"

# How each measure of a scope family reads, and at what precision.
MEASURE_FORMATS: dict[str, Fmt] = {
    "Units": Fmt.UNITS,
    "ACB": Fmt.MONEY,
    "Delta": Fmt.MONEY_SIGNED,
    "Avg": Fmt.PRICE,
    "Gain": Fmt.MONEY_SIGNED,
}

# The buildup names its dates the way the ledger and the `Txns` sheet do, so a
# reader filtering one sheet knows what to filter on in the next.
_TXN_DATE = str(Column.Txn.TXN_DATE)
_SETTLE_DATE = str(Column.Txn.SETTLE_DATE)

_CONVERSION_NOTE = (
    "CAD cost base is converted at each transaction's settle date, not at today's rate."
)


def acb_buildup_table(
    rows: pd.DataFrame,
    *,
    scope: Scope,
    name: str,
    currency: str = "both",
    notes: Sequence[str] = (),
) -> Table:
    """Symbol's cost-base buildup out as a table.

    Written oldest first, so the running `Held`, `ACB` and `Avg` columns read as
    the accumulation they are.

    Args:
        rows: The symbol's rows from the master frame.
        scope: The pool grain being reported.
        name: What the sheet is called.
        currency: `CAD`, `USD` or `both`.
        notes: Lines to disclose above the ones the table derives itself.

    Returns:
        The table, in settle-date order.
    """
    cad, usd = families(currency, usd=has_usd(rows, (scope,)))
    columns = [
        Col(_TXN_DATE, Fmt.DATE),
        Col(_SETTLE_DATE, Fmt.DATE),
        Col("TxnId", Fmt.ID),
        Col("Action"),
        Col("Units", Fmt.UNITS, quiet=True),
        Col("Held", Fmt.UNITS),
        Col("Price", Fmt.PRICE, quiet=True),
        Col("Amount", Fmt.MONEY_SIGNED),
        Col("Fee", Fmt.MONEY, quiet=True),
    ]
    for measure in ("Delta", "ACB", "Avg", "Gain"):
        if cad:
            columns.append(Col(measure, MEASURE_FORMATS[measure]))
        if usd:
            columns.append(Col(f"{measure} USD", MEASURE_FORMATS[measure]))
    if cad:
        columns.append(Col("Proceeds", Fmt.MONEY, quiet=True))
    if usd:
        columns.append(Col("Rate", Fmt.RATE))
    columns.append(Col("Flags", width=16))

    ordered = rows.sort_values(
        [str(Column.Txn.SETTLE_DATE), str(Column.Txn.TXN_ID)],
        kind="stable",
    )
    built = tuple(columns)
    return Table(
        name=name,
        columns=built,
        rows=tuple(
            row_of(built, _buildup_cells(record, scope))
            for record in ordered.to_dict("records")
        ),
        notes=(*notes, *acb_notes(rows)),
    )


def acb_summary_table(
    rows: pd.DataFrame,
    *,
    scope: Scope,
    name: str,
    currency: str = "both",
    notes: Sequence[str] = (),
) -> Table:
    """Collapse a pool to one row per symbol: its closing position.

    Args:
        rows: The pool's rows from the master frame.
        scope: The pool grain being reported.
        name: What the sheet is called.
        currency: `CAD`, `USD` or `both`. `USD` drops the CAD-denominated
            holdings, which have no USD figures to show.
        notes: Lines to disclose above the ones the table derives itself.

    Returns:
        The table, open positions first and alphabetical within each group.
    """
    summary = acb_summary_frame(rows)
    cad, usd = families(currency, usd=has_usd(summary, (scope,)))
    if usd and not cad and not summary.empty:
        summary = summary[summary[scope_column(scope, "ACB" + USD)].notna()]

    columns: list[Col] = [Col("Symbol"), Col("Units", Fmt.UNITS)]
    for measure in ("ACB", "Avg", "Gain"):
        if cad:
            columns.append(Col(measure, MEASURE_FORMATS[measure]))
        if usd:
            columns.append(Col(f"{measure} USD", MEASURE_FORMATS[measure]))

    built = tuple(columns)
    return Table(
        name=name,
        columns=built,
        rows=tuple(
            row_of(built, _summary_cells(record, scope))
            for record in _open_first(summary, scope).to_dict("records")
        ),
        notes=(*notes, *acb_notes(rows)),
    )


COST_BASE_COLUMNS: tuple[Col, ...] = (
    Col("Pool", width=18),
    Col("Scope", width=10),
    Col("Symbol"),
    # A closed position is a realized gain and nothing else, so its zero units,
    # cost and average stay blank rather than reading as something still held.
    Col("Units", Fmt.UNITS, quiet=True),
    Col("ACB", Fmt.MONEY, quiet=True),
    Col("Avg", Fmt.PRICE, quiet=True),
    Col("Gain", Fmt.MONEY_SIGNED),
)


def cost_base_table(
    frame: pd.DataFrame,
    *,
    name: str = "Cost Base",
    notes: Sequence[str] = (),
) -> Table:
    """List every pool's closing cost base, in the currency it is taxed in.

    Args:
        frame: The whole master frame.
        name: What the sheet is called.
        notes: Lines to disclose above the ones the table derives itself.

    Returns:
        The table, one row per pool and symbol: the portfolio first, then each
        account type, then each account. Open positions lead within each pool.
    """
    pools: list[tuple[str, Scope, pd.DataFrame]] = [
        (FOLIO_VIEW.label, Scope.FOLIO, acb_summary_frame(frame)),
    ]
    for scope in (Scope.TYPE, Scope.ACCOUNT):
        for pool, summary in sorted(acb_summary_frames_by_pool(frame, scope).items()):
            label = type_view(pool).label if scope is Scope.TYPE else pool
            pools.append((label, scope, summary))

    rows = [
        row_of(COST_BASE_COLUMNS, _cost_base_cells(label, scope, record))
        for label, scope, summary in pools
        for record in _open_first(summary, scope).to_dict("records")
    ]
    return Table(
        name=name,
        columns=COST_BASE_COLUMNS,
        rows=tuple(rows),
        notes=(*notes, _CONVERSION_NOTE),
        tab_color=COST_BASE_TAB,
        freeze=3,
    )


def _cost_base_cells(
    label: str,
    scope: Scope,
    record: Mapping[Hashable, Any],
) -> dict[str, object]:
    """Read one symbol's closing position in one pool, in CAD."""
    return {
        "Pool": label,
        "Scope": SCOPE_NAMES[scope],
        "Symbol": record["Symbol"],
        "Units": record[scope_column(scope, "Units")],
        "ACB": record[scope_column(scope, "ACB")],
        "Avg": record[scope_column(scope, "Avg")],
        "Gain": record[scope_column(scope, "Gain")],
    }


def families(currency: str, *, usd: bool) -> tuple[bool, bool]:
    """Decide which currency variants the sheet carries.

    Args:
        currency: The `--currency` argument: `CAD`, `USD` or `both`.
        usd: Whether the rows hold any USD-denominated figures at all.

    Returns:
        Whether to carry the CAD columns, and whether to carry the USD ones.
    """
    choice = currency.strip().upper()
    if choice == "USD":
        return False, True
    if choice == "CAD":
        return True, False
    return True, usd


def has_usd(frame: pd.DataFrame, scopes: Sequence[Scope]) -> bool:
    """Report whether any row carries a USD-denominated cost base."""
    return any(
        column in frame.columns and frame[column].notna().any()
        for column in (scope_column(scope, "ACB" + USD) for scope in scopes)
    )


def _buildup_cells(record: Mapping[Hashable, Any], scope: Scope) -> dict[str, object]:
    """Read one transaction into the buildup's cells."""
    cells: dict[str, object] = {
        _TXN_DATE: record[str(Column.Txn.TXN_DATE)],
        _SETTLE_DATE: record[str(Column.Txn.SETTLE_DATE)],
        "TxnId": record[str(Column.Txn.TXN_ID)],
        "Action": record[str(Column.Txn.ACTION)],
        "Units": record[str(Column.Txn.UNITS)],
        "Held": record[scope_column(scope, "Units")],
        "Price": record[str(Column.Txn.PRICE)],
        "Amount": record[str(Column.Txn.AMOUNT)],
        "Fee": record[str(Column.Txn.FEE)],
        "Proceeds": record["Proceeds"],
        "Rate": record["FXRate"],
        "Flags": record["Flags"],
    }
    for measure in ("Delta", "ACB", "Avg", "Gain"):
        cells[measure] = record[scope_column(scope, measure)]
        cells[f"{measure} USD"] = record[scope_column(scope, measure + USD)]
    return cells


def _summary_cells(record: Mapping[Hashable, Any], scope: Scope) -> dict[str, object]:
    """Read one symbol's closing position into the summary's cells."""
    cells: dict[str, object] = {
        "Symbol": record["Symbol"],
        "Units": record[scope_column(scope, "Units")],
    }
    for measure in ("ACB", "Avg", "Gain"):
        cells[measure] = record[scope_column(scope, measure)]
        cells[f"{measure} USD"] = record[scope_column(scope, measure + USD)]
    return cells


def _open_first(summary: pd.DataFrame, scope: Scope) -> pd.DataFrame:
    """Sink closed positions to the bottom, alphabetical within each group.

    A closed position is history: a realized gain and four blanks. Interleaving
    those alphabetically pushes what is actually held apart.
    """
    if summary.empty:
        return summary
    units = scope_column(scope, "Units")
    ordered = summary.assign(_closed=summary[units].fillna(0) == 0)
    return ordered.sort_values(["_closed", "Symbol"], kind="stable").drop(
        columns="_closed",
    )


def acb_notes(rows: pd.DataFrame) -> list[str]:
    """Roll the diagnostics up, and say what the CAD figures were converted at."""
    notes: list[str] = []
    flags: dict[str, int] = {}
    if "Flags" in rows.columns:
        for value in rows["Flags"]:
            for code in str(value or "").split(","):
                if code:
                    flags[code] = flags.get(code, 0) + 1
    if flags:
        listed = "  ".join(f"{code} x{count}" for code, count in sorted(flags.items()))
        notes.append(f"Flags: {listed}")
    notes.append(_CONVERSION_NOTE)
    return notes
