"""Turn a replay into the wide master frame the CLI and exports render from.

The frame is the display and export boundary. The replay itself is exact
Decimal arithmetic end to end; numbers become float here, where the smallest
column is displayed to 6dp and float64 carries fifteen significant figures.

Conventions for the per-scope columns:

- **CAD is unsuffixed.** It is always populated, and it is the tax currency.
- **`_USD` marks the original-currency variant**, populated only for
  USD-denominated holdings and blank otherwise. The suffix applies to *every*
  USD column, not only where acronyms would collide -- one rule, no exceptions.
- **`Delta` means change in cost base.** The ACB context is implied by the family.

There are deliberately no per-type columns (`TfsaACB`, `NregACB`): a transaction
belongs to one type, so those would leave a single populated cell per row and
grow with every account type ever opened. Filter on `AcctType` instead.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd

from utils.constants import Column, Scope
from utils.numeric import safe_div

if TYPE_CHECKING:
    from engine.events import ComputedRow, ReplayResult, ScopeMeasures

# Source columns carried straight through from Txns.
SOURCE_COLUMNS = [
    str(Column.Txn.TXN_ID),
    str(Column.Txn.TXN_DATE),
    str(Column.Txn.SETTLE_DATE),
    str(Column.Txn.ACTION),
    str(Column.Txn.TICKER),
    str(Column.Txn.ACCOUNT),
    str(Column.Txn.CURRENCY),
    str(Column.Txn.AMOUNT),
    str(Column.Txn.PRICE),
    str(Column.Txn.UNITS),
    str(Column.Txn.FEE),
]

# Derived context that is not scope-specific.
CONTEXT_COLUMNS = ["Symbol", "AcctType", "Impact", "FXRate", "FXDate", "Flags"]

# Measures shared by every scope.
SHARED_COLUMNS = ["Proceeds", "Proceeds_USD", "Dividend", "Dividend_USD"]

# The prefix each scope's column family carries.
SCOPE_PREFIX: dict[Scope, str] = {
    Scope.ACCOUNT: "Acct",
    Scope.TYPE: "Type",
    Scope.FOLIO: "Folio",
}

# Measure suffix -> the `ScopeMeasures` attribute behind it. CAD first, then the
# original-currency variant, matching how the CLI renders them side by side.
SCOPE_MEASURES: list[tuple[str, str]] = [
    ("Units", "units"),
    ("ACB", "acb_cad"),
    ("ACB_USD", "acb_usd"),
    ("Delta", "delta_cad"),
    ("Delta_USD", "delta_usd"),
    ("Avg", "avg_cad"),
    ("Avg_USD", "avg_usd"),
    ("Gain", "gain_cad"),
    ("Gain_USD", "gain_usd"),
]


def scope_column(scope: Scope, measure: str) -> str:
    """Name the column holding one measure at one pool grain.

    Args:
        scope: The pool grain.
        measure: A suffix from `SCOPE_MEASURES`, such as `"ACB"` or `"Avg_USD"`.

    Returns:
        The column name, for example `AcctACB` or `FolioAvg_USD`.
    """
    return f"{SCOPE_PREFIX[scope]}{measure}"


def _number(value: Decimal | None) -> float | None:
    """Render a Decimal for the frame, keeping None as a genuine blank."""
    return None if value is None else float(value)


def _scope_cells(
    computed: ComputedRow,
    scope: Scope,
    measures: ScopeMeasures,
) -> dict[str, float | None]:
    """Emit one scope's nine measures, blanking what does not apply."""
    tracked = computed.symbol is not None
    cells: dict[str, float | None] = {}
    for suffix, attribute in SCOPE_MEASURES:
        column = scope_column(scope, suffix)
        if not tracked or (suffix.endswith("_USD") and not computed.is_usd):
            cells[column] = None
            continue
        cells[column] = _number(getattr(measures, attribute))
    return cells


def _row_record(computed: ComputedRow) -> dict[str, object]:
    """Flatten one `ComputedRow` into the master frame's columns."""
    row = computed.row
    record: dict[str, object] = {
        str(Column.Txn.TXN_ID): row.txn_id,
        str(Column.Txn.TXN_DATE): row.txn_date,
        str(Column.Txn.SETTLE_DATE): row.settle_date,
        str(Column.Txn.ACTION): str(row.action),
        str(Column.Txn.TICKER): row.ticker,
        str(Column.Txn.ACCOUNT): row.account,
        str(Column.Txn.CURRENCY): str(row.currency),
        str(Column.Txn.AMOUNT): float(row.amount),
        str(Column.Txn.PRICE): float(row.price),
        str(Column.Txn.UNITS): float(row.units),
        str(Column.Txn.FEE): float(row.fee),
        "Symbol": computed.symbol,
        "AcctType": str(computed.acct_type),
        "Impact": str(computed.impact),
        "FXRate": _number(computed.fx_rate),
        "FXDate": computed.fx_date,
        "Flags": ",".join(str(code) for code in computed.flags),
        "Proceeds": _number(computed.proceeds_cad),
        "Proceeds_USD": _number(computed.proceeds_usd),
        "Dividend": _number(computed.dividend_cad),
        "Dividend_USD": _number(computed.dividend_usd),
    }
    for scope in Scope:
        record.update(_scope_cells(computed, scope, computed.measures(scope)))
    return record


def master_columns() -> list[str]:
    """Every column of the master frame, in display order.

    Returns:
        Source columns, then derived context, then the shared measures, then
        each scope's nine-measure family.
    """
    columns = [*SOURCE_COLUMNS, *CONTEXT_COLUMNS, *SHARED_COLUMNS]
    for scope in Scope:
        columns.extend(scope_column(scope, suffix) for suffix, _ in SCOPE_MEASURES)
    return columns


def index_by_txn_id(frame: pd.DataFrame) -> pd.DataFrame:
    """Index a master frame by `TxnId` while keeping it as a column.

    The index name is cleared deliberately: leaving it set would make `TxnId`
    both an index level and a column label, which `sort_values` and `groupby`
    then refuse to disambiguate. Applied on a fresh build and again after a
    cache read, since Parquet stores no index.

    Args:
        frame: A master frame, indexed however it arrived.

    Returns:
        The same frame indexed by TxnId, ascending.
    """
    indexed = frame.set_index(str(Column.Txn.TXN_ID), drop=False).sort_index()
    indexed.index.name = None
    return indexed


def master_frame(result: ReplayResult) -> pd.DataFrame:
    """Build the wide master frame, one row per transaction.

    Args:
        result: A completed replay.

    Returns:
        A DataFrame indexed by `TxnId`, strictly 1:1 with `Txns`, holding the
        source columns alongside every derived and per-scope measure.
    """
    columns = master_columns()
    if not result.rows:
        return index_by_txn_id(pd.DataFrame(columns=columns))

    return index_by_txn_id(
        pd.DataFrame(
            [_row_record(computed) for computed in result.rows],
            columns=columns,
        ),
    )


def acb_summary_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Collapse the master frame to one row per symbol, all three scopes.

    The final state of each pool is the last row that touched it, in trade-date
    order.

    Args:
        frame: A master frame.

    Returns:
        One row per symbol carrying units, cost base, average cost and realized
        gain at all three grains, sorted by symbol.
    """
    if frame.empty or "Symbol" not in frame.columns:
        return pd.DataFrame(columns=["Symbol", *_summary_columns()])

    # TxnId is both the index and a column on the master frame, so drop the
    # index to avoid ambiguity for sort_values and groupby.
    tracked = frame[frame["Symbol"].notna()].reset_index(drop=True)
    if tracked.empty:
        return pd.DataFrame(columns=["Symbol", *_summary_columns()])

    records: list[dict[str, object]] = []
    ordered = tracked.sort_values(
        [str(Column.Txn.TXN_DATE), str(Column.Txn.TXN_ID)],
        kind="stable",
    )
    for symbol, rows in ordered.groupby("Symbol", sort=True):
        last = rows.iloc[-1]
        record: dict[str, object] = {"Symbol": symbol}
        for scope in Scope:
            for suffix in ("Units", "ACB", "ACB_USD", "Gain", "Gain_USD"):
                column = scope_column(scope, suffix)
                record[column] = last[column]
            # Both averages are computed rather than carried
            for suffix in ("Avg", "Avg_USD"):
                record[scope_column(scope, suffix)] = _average(
                    last[scope_column(scope, "Units")],
                    last[scope_column(scope, suffix.replace("Avg", "ACB"))],
                )
        records.append(record)

    return pd.DataFrame(records, columns=["Symbol", *_summary_columns()])


def _summary_columns() -> list[str]:
    """List the per-scope columns the summary frame carries."""
    columns: list[str] = []
    for scope in Scope:
        columns.extend(
            scope_column(scope, suffix)
            for suffix in (
                "Units",
                "ACB",
                "ACB_USD",
                "Avg",
                "Avg_USD",
                "Gain",
                "Gain_USD",
            )
        )
    return columns


def _average(units: float | None, acb: float | None) -> float | None:
    """Average cost from a summary row's units and cost base."""
    if units is None or acb is None or pd.isna(units) or pd.isna(acb):
        return None
    return float(safe_div(Decimal(str(acb)), Decimal(str(units))))
