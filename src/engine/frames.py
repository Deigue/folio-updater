"""Turn a replay into the wide ledger the CLI and exports render from.

The frame is the display and export boundary. The replay itself is exact
Decimal arithmetic end to end; numbers become float here, where the smallest
column is displayed to 6dp and float64 carries fifteen significant figures.

Conventions for the per-grain columns:

- **CAD is unsuffixed.** It is always populated, and it is the tax currency.
- **`_USD` marks the original-currency variant**, populated only for
  USD-denominated holdings and blank otherwise. The suffix applies to *every*
  USD column.
- **`Delta` means change in cost base.** The ACB context is implied by the family.
- **`Realized` is the running realized gain, `Gain` what this row earned.** A sale
  always has one, even at zero. A BUY's is blank here and added at valuation.

There are deliberately no per-type columns (`TfsaACB`, `NregACB`): a transaction
belongs to one type, so those would leave a single populated cell per row and
grow with every account type ever opened. Filter on `AcctType` instead.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd

from domain import Action, Column, Grain
from domain.numeric import ZERO, safe_div
from engine.replay import intraday_rank

if TYPE_CHECKING:
    from engine.types import GrainMeasures, LedgerRow, ReplayResult

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

# Derived context that is not grain-specific.
CONTEXT_COLUMNS = [
    str(Column.Ledger.SYMBOL),
    str(Column.Ledger.ACCT_TYPE),
    str(Column.Ledger.IMPACT),
    str(Column.Ledger.FX_RATE),
    str(Column.Ledger.FX_DATE),
    str(Column.Ledger.FLAGS),
]

# Measures shared by every grain.
SHARED_COLUMNS = [
    str(Column.Ledger.PROCEEDS),
    str(Column.Ledger.PROCEEDS_USD),
    str(Column.Ledger.DIVIDEND),
    str(Column.Ledger.DIVIDEND_USD),
]

# The column a pool at each grain is named by.
POOL_COLUMN: dict[Grain, str] = {
    Grain.ACCOUNT: str(Column.Txn.ACCOUNT),
    Grain.TYPE: str(Column.Ledger.ACCT_TYPE),
}

# The prefix each grain's column family carries.
GRAIN_PREFIX: dict[Grain, str] = {
    Grain.ACCOUNT: "Acct",
    Grain.TYPE: "Type",
    Grain.FOLIO: "Folio",
}

# Measure suffix -> the `GrainMeasures` attribute behind it. CAD first, then the
# original-currency variant, matching how the CLI renders them side by side.
GRAIN_MEASURES: list[tuple[str, str]] = [
    ("Units", "units"),
    ("ACB", "acb_cad"),
    ("ACB_USD", "acb_usd"),
    ("Delta", "delta_cad"),
    ("Delta_USD", "delta_usd"),
    ("Avg", "avg_cad"),
    ("Avg_USD", "avg_usd"),
    ("Realized", "realized_cad"),
    ("Realized_USD", "realized_usd"),
    ("Gain", "gain_cad"),
    ("Gain_USD", "gain_usd"),
]


def grain_column(grain: Grain, measure: str) -> str:
    """Name the column holding one measure at one pool grain.

    Args:
        grain: The pool grain.
        measure: A suffix from `GRAIN_MEASURES`, such as `"ACB"` or `"Avg_USD"`.

    Returns:
        The column name, for example `AcctACB` or `FolioAvg_USD`.
    """
    return f"{GRAIN_PREFIX[grain]}{measure}"


# The measures a summary carries: each grain measure that is a state, not a change.
_SUMMARY_SUFFIXES = tuple(
    suffix for suffix, _ in GRAIN_MEASURES if not suffix.startswith(("Delta", "Gain"))
)

# A summary frame's columns: the symbol, then each grain's summary measures.
_SUMMARY_COLUMNS = [
    str(Column.Ledger.SYMBOL),
    *(grain_column(grain, suffix) for grain in Grain for suffix in _SUMMARY_SUFFIXES),
]


def _number(value: Decimal | None) -> float | None:
    """Render a Decimal for the frame, keeping None as a genuine blank."""
    return None if value is None else float(value)


def _grain_cells(
    computed: LedgerRow,
    grain: Grain,
    measures: GrainMeasures,
) -> dict[str, float | None]:
    """Emit one grain's measures, blanking what does not apply."""
    tracked = computed.symbol is not None
    sale = computed.row.action is Action.SELL
    cells: dict[str, float | None] = {}
    for suffix, attribute in GRAIN_MEASURES:
        column = grain_column(grain, suffix)
        if not tracked or (suffix.endswith("_USD") and not measures.usd):
            cells[column] = None
            continue
        value = getattr(measures, attribute)
        earned_nothing = suffix.startswith("Gain") and value == ZERO and not sale
        cells[column] = None if earned_nothing else _number(value)
    return cells


def _row_record(computed: LedgerRow) -> dict[str, object]:
    """Flatten one `LedgerRow` into the ledger's columns."""
    row = computed.row
    record: dict[str, object] = {
        str(Column.Txn.TXN_ID): row.txn_id,
        str(Column.Txn.TXN_DATE): row.txn_date,
        str(Column.Txn.SETTLE_DATE): row.settle_date,
        str(Column.Txn.ACTION): str(row.action),
        str(Column.Txn.TICKER): row.ticker,
        str(Column.Txn.ACCOUNT): row.account,
        str(Column.Txn.CURRENCY): str(row.currency),
        str(Column.Txn.AMOUNT): float(row.amount or ZERO),
        str(Column.Txn.PRICE): float(row.price or ZERO),
        str(Column.Txn.UNITS): float(row.units or ZERO),
        str(Column.Txn.FEE): float(row.fee or ZERO),
        str(Column.Ledger.SYMBOL): computed.symbol,
        str(Column.Ledger.ACCT_TYPE): str(computed.acct_type),
        str(Column.Ledger.IMPACT): str(computed.impact),
        str(Column.Ledger.FX_RATE): _number(computed.fx_rate),
        str(Column.Ledger.FX_DATE): computed.fx_date,
        str(Column.Ledger.FLAGS): ",".join(str(code) for code in computed.flags),
        str(Column.Ledger.PROCEEDS): _number(computed.proceeds_cad),
        str(Column.Ledger.PROCEEDS_USD): _number(computed.proceeds_usd),
        str(Column.Ledger.DIVIDEND): _number(computed.dividend_cad),
        str(Column.Ledger.DIVIDEND_USD): _number(computed.dividend_usd),
    }
    for grain in Grain:
        record.update(_grain_cells(computed, grain, computed.measures(grain)))
    return record


def ledger_columns() -> list[str]:
    """Every column of the ledger, in display order.

    Returns:
        Source columns, then derived context, then the shared measures, then
        each grain's family of measures.
    """
    columns = [*SOURCE_COLUMNS, *CONTEXT_COLUMNS, *SHARED_COLUMNS]
    for grain in Grain:
        columns.extend(grain_column(grain, suffix) for suffix, _ in GRAIN_MEASURES)
    return columns


def index_by_txn_id(frame: pd.DataFrame) -> pd.DataFrame:
    """Index a ledger by `TxnId` while keeping it as a column.

    The index name is cleared deliberately: leaving it set would make `TxnId`
    both an index level and a column label, which `sort_values` and `groupby`
    then refuse to disambiguate. Applied on a fresh build and again after a
    cache read, since Parquet stores no index.

    Args:
        frame: A ledger, indexed however it arrived.

    Returns:
        The same frame indexed by TxnId, ascending.
    """
    indexed = frame.set_index(str(Column.Txn.TXN_ID), drop=False).sort_index()
    indexed.index.name = None
    return indexed


def ledger_frame(result: ReplayResult) -> pd.DataFrame:
    """Build the wide ledger, one row per transaction.

    Args:
        result: A completed replay.

    Returns:
        A DataFrame indexed by `TxnId`, strictly 1:1 with `Txns`, holding the
        source columns alongside every derived and per-grain measure.
    """
    columns = ledger_columns()
    if not result.rows:
        return index_by_txn_id(pd.DataFrame(columns=columns))

    return index_by_txn_id(
        pd.DataFrame(
            [_row_record(computed) for computed in result.rows],
            columns=columns,
        ),
    )


def acb_summary_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Collapse the ledger to one row per symbol, all three grains.

    The final state of each pool is the last row that touched it, in transaction-date
    order.

    Args:
        frame: A ledger.

    Returns:
        One row per symbol carrying units, cost base, average cost and realized
        gain at all three grains, sorted by symbol.
    """
    if frame.empty or Column.Ledger.SYMBOL not in frame.columns:
        return pd.DataFrame(columns=_SUMMARY_COLUMNS)

    tracked = _tracked(frame)
    if tracked.empty:
        return pd.DataFrame(columns=_SUMMARY_COLUMNS)

    records = [
        _summary_record(symbol, rows.iloc[-1])
        for symbol, rows in replay_ordered(tracked).groupby(
            str(Column.Ledger.SYMBOL),
            sort=True,
        )
    ]
    return pd.DataFrame(records, columns=_SUMMARY_COLUMNS)


def _tracked(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep the rows that track a symbol, with the index dropped.

    TxnId is both the index and a column on the ledger, so the index goes to
    avoid ambiguity for `sort_values` and `groupby`.
    """
    return frame[frame[Column.Ledger.SYMBOL].notna()].reset_index(drop=True)


_RANK = "_intraday_rank"


def replay_ordered(frame: pd.DataFrame) -> pd.DataFrame:
    """Put ledger rows in the exact order the cost base was replayed in.

    Transaction date first, then the day's actions in the order the replay applies
    them (a purchase before a sale, so a same-day round trip is not an
    oversell), then TxnId.

    Args:
        frame: Rows from the ledger.

    Returns:
        The same rows, reordered.
    """
    ranked = frame.assign(
        **{_RANK: frame[str(Column.Txn.ACTION)].map(intraday_rank)},
    )
    return ranked.sort_values(
        [str(Column.Txn.TXN_DATE), _RANK, str(Column.Txn.TXN_ID)],
        kind="stable",
    ).drop(columns=_RANK)


def _summary_record(symbol: object, last: pd.Series) -> dict[str, object]:
    """Read one symbol's final figures off the last row that touched it."""
    record: dict[str, object] = {str(Column.Ledger.SYMBOL): symbol}
    for grain in Grain:
        for suffix in _SUMMARY_SUFFIXES:
            column = grain_column(grain, suffix)
            if suffix.startswith("Avg"):
                # Both averages are computed rather than carried
                record[column] = _average(
                    last[grain_column(grain, "Units")],
                    last[grain_column(grain, suffix.replace("Avg", "ACB"))],
                )
            else:
                record[column] = last[column]
    return record


def _average(units: float | None, acb: float | None) -> float | None:
    """Average cost from a summary row's units and cost base."""
    if units is None or acb is None or pd.isna(units) or pd.isna(acb):
        return None
    return float(safe_div(Decimal(str(acb)), Decimal(str(units))))
