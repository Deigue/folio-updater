"""The cost-base sheets: a symbol's buildup, a pool summary, and the Cost Base."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import Column, Grain
from engine.acb_report import AcbReport
from engine.frames import grain_column
from exporters.excel_style import COST_BASE_TAB
from exporters.sheet_spec import Col, Fmt, Sheet, SheetTable, row_of
from exporters.sheets.summary import GRAIN_NAMES

if TYPE_CHECKING:
    from collections.abc import Hashable, Mapping, Sequence
    from typing import Any

    import pandas as pd

USD = "_USD"
COST_BASE_SHEET = "Cost Base"

# How each measure of a grain family reads, and at what precision.
MEASURE_FORMATS: dict[str, Fmt] = {
    "Units": Fmt.UNITS,
    "ACB": Fmt.MONEY,
    "Delta": Fmt.MONEY_SIGNED,
    "Avg": Fmt.PRICE,
    "Realized": Fmt.MONEY_SIGNED,
    "Gain": Fmt.MONEY_SIGNED,
    "GainPct": Fmt.PERCENT_SIGNED,
}

# The buildup's per-grain measures, in the order they are laid out.
_BUILDUP_MEASURES = ("Delta", "ACB", "Avg", "Realized", "Gain", "GainPct")


def measure_header(measure: str) -> str:
    """Spell a measure, or its `_USD` variant, the way a reader should see it."""
    return measure.replace("Pct", "%").replace(USD, " USD")


# The buildup names its dates the way the ledger and the `Txns` sheet do, so a
# reader filtering one sheet knows what to filter on in the next.
_TXN_DATE = str(Column.Txn.TXN_DATE)
_SETTLE_DATE = str(Column.Txn.SETTLE_DATE)

_CONVERSION_NOTE = (
    "CAD cost base is converted at each transaction's settlement date, "
    "not at today's rate."
)


def acb_buildup_sheet(
    report: AcbReport,
    *,
    name: str,
    notes: Sequence[str] = (),
) -> Sheet:
    """Print a symbol's cost-base buildup as a sheet.

    Listed by settlement date, the way adjustedcostbase.ca lists them. The cost
    base itself was replayed in transaction-date order, so the running `Held`, `ACB`
    and `Avg` columns can step out of sequence where the two orders differ.

    Args:
        report: The symbol's report, its rows oldest first.
        name: What the sheet is called.
        notes: Lines to disclose above the ones the table derives itself.

    Returns:
        The sheet, in settlement-date order.
    """
    grain = report.pool.grain
    cad, usd = report.currencies
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
    for measure in _BUILDUP_MEASURES:
        if cad:
            columns.append(Col(measure_header(measure), MEASURE_FORMATS[measure]))
        if usd:
            columns.append(
                Col(measure_header(measure + USD), MEASURE_FORMATS[measure]),
            )
    if cad:
        columns.append(Col("Proceeds", Fmt.MONEY, quiet=True))
    if usd:
        columns.append(Col("Rate", Fmt.RATE))
    columns.append(Col("Flags", width=16))

    built = tuple(columns)
    return Sheet(
        name=name,
        tables=(
            SheetTable(
                built,
                tuple(
                    row_of(built, _buildup_cells(record, grain))
                    for record in report.rows.to_dict("records")
                ),
                notes=(*notes, *acb_notes(report)),
            ),
        ),
    )


def acb_summary_sheet(
    report: AcbReport,
    *,
    name: str,
    notes: Sequence[str] = (),
) -> Sheet:
    """Collapse a pool to one row per symbol: its closing holding.

    Args:
        report: The pool's cost base summary report.
        name: What the sheet is called.
        notes: Lines to disclose above the ones the table derives itself.

    Returns:
        The sheet, one row per holding in the report's order.
    """
    grain = report.pool.grain
    cad, usd = report.currencies
    columns: list[Col] = [Col("Symbol"), Col("Units", Fmt.UNITS)]
    for measure in ("ACB", "Avg", "Realized"):
        if cad:
            columns.append(Col(measure, MEASURE_FORMATS[measure]))
        if usd:
            columns.append(Col(f"{measure} USD", MEASURE_FORMATS[measure]))

    built = tuple(columns)
    return Sheet(
        name=name,
        tables=(
            SheetTable(
                built,
                tuple(
                    row_of(built, _summary_cells(record, grain))
                    for record in report.holdings.to_dict("records")
                ),
                notes=(*notes, *acb_notes(report)),
            ),
        ),
    )


COST_BASE_COLUMNS: tuple[Col, ...] = (
    Col("Pool", width=18),
    Col("Grain", width=10),
    Col("Symbol"),
    # A closed holding is a realized gain and nothing else, so its zero units,
    # cost and average stay blank rather than reading as something still held.
    Col("Units", Fmt.UNITS, quiet=True),
    Col("ACB", Fmt.MONEY, quiet=True),
    Col("Avg", Fmt.PRICE, quiet=True),
    Col("Realized", Fmt.MONEY_SIGNED),
)


def cost_base_sheet(
    frame: pd.DataFrame,
    *,
    name: str = COST_BASE_SHEET,
    notes: Sequence[str] = (),
) -> Sheet:
    """List every pool's closing cost base, in the currency it is taxed in.

    Args:
        frame: The whole ledger.
        name: What the sheet is called.
        notes: Lines to disclose above the ones the table derives itself.

    Returns:
        The sheet, one row per pool and symbol: the folio first, then each
        account type, then each account. Open holdings lead within each pool.
    """
    rows = [
        row_of(
            COST_BASE_COLUMNS,
            _cost_base_cells(report.pool.label, report.pool.grain, record),
        )
        for report in AcbReport.for_every_pool(frame)
        for record in report.holdings.to_dict("records")
    ]
    return Sheet(
        name=name,
        tables=(
            SheetTable(
                COST_BASE_COLUMNS,
                tuple(rows),
                notes=(*notes, _CONVERSION_NOTE),
            ),
        ),
        tab_color=COST_BASE_TAB,
        freeze=3,
    )


def _cost_base_cells(
    label: str,
    grain: Grain,
    record: Mapping[Hashable, Any],
) -> dict[str, object]:
    """Read one symbol's closing holding in one pool, in CAD."""
    return {
        "Pool": label,
        "Grain": GRAIN_NAMES[grain],
        "Symbol": record["Symbol"],
        "Units": record[grain_column(grain, "Units")],
        "ACB": record[grain_column(grain, "ACB")],
        "Avg": record[grain_column(grain, "Avg")],
        "Realized": record[grain_column(grain, "Realized")],
    }


def _buildup_cells(record: Mapping[Hashable, Any], grain: Grain) -> dict[str, object]:
    """Read one transaction into the buildup's cells."""
    cells: dict[str, object] = {
        _TXN_DATE: record[str(Column.Txn.TXN_DATE)],
        _SETTLE_DATE: record[str(Column.Txn.SETTLE_DATE)],
        "TxnId": record[str(Column.Txn.TXN_ID)],
        "Action": record[str(Column.Txn.ACTION)],
        "Units": record[str(Column.Txn.UNITS)],
        "Held": record[grain_column(grain, "Units")],
        "Price": record[str(Column.Txn.PRICE)],
        "Amount": record[str(Column.Txn.AMOUNT)],
        "Fee": record[str(Column.Txn.FEE)],
        "Proceeds": record["Proceeds"],
        "Rate": record["FXRate"],
        "Flags": record["Flags"],
    }
    for measure in _BUILDUP_MEASURES:
        for variant in (measure, measure + USD):
            cells[measure_header(variant)] = record[grain_column(grain, variant)]
    return cells


def _summary_cells(record: Mapping[Hashable, Any], grain: Grain) -> dict[str, object]:
    """Read one symbol's closing holding into the summary's cells."""
    cells: dict[str, object] = {
        "Symbol": record["Symbol"],
        "Units": record[grain_column(grain, "Units")],
    }
    for measure in ("ACB", "Avg", "Realized"):
        cells[measure] = record[grain_column(grain, measure)]
        cells[f"{measure} USD"] = record[grain_column(grain, measure + USD)]
    return cells


def acb_notes(report: AcbReport) -> list[str]:
    """Roll the diagnostics up, and say what the CAD figures were converted at."""
    notes: list[str] = []
    if report.flag_counts:
        listed = "  ".join(
            f"{code} x{count}" for code, count in report.flag_counts.items()
        )
        notes.append(f"Flags: {listed}")
    notes.append(_CONVERSION_NOTE)
    return notes
