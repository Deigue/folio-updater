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

There are deliberately no per-type columns (`TfsaACB`, `NregACB`): a transaction
belongs to one type, so those would leave a single populated cell per row and
grow with every account type ever opened. Filter on `AcctType` instead.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd

from domain import Column, Grain
from domain.numeric import safe_div
from engine.replay import intraday_rank

if TYPE_CHECKING:
    from engine.types import ComputedRow, GrainMeasures, ReplayResult

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
CONTEXT_COLUMNS = ["Symbol", "AcctType", "Impact", "FXRate", "FXDate", "Flags"]

# Measures shared by every grain.
SHARED_COLUMNS = ["Proceeds", "Proceeds_USD", "Dividend", "Dividend_USD"]

# The column a pool at each grain is named by.
POOL_COLUMN: dict[Grain, str] = {
    Grain.ACCOUNT: str(Column.Txn.ACCOUNT),
    Grain.TYPE: "AcctType",
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


def _number(value: Decimal | None) -> float | None:
    """Render a Decimal for the frame, keeping None as a genuine blank."""
    return None if value is None else float(value)


def _grain_cells(
    computed: ComputedRow,
    grain: Grain,
    measures: GrainMeasures,
) -> dict[str, float | None]:
    """Emit one grain's nine measures, blanking what does not apply."""
    tracked = computed.symbol is not None
    cells: dict[str, float | None] = {}
    for suffix, attribute in GRAIN_MEASURES:
        column = grain_column(grain, suffix)
        if not tracked or (suffix.endswith("_USD") and not computed.is_usd):
            cells[column] = None
            continue
        cells[column] = _number(getattr(measures, attribute))
    return cells


def _row_record(computed: ComputedRow) -> dict[str, object]:
    """Flatten one `ComputedRow` into the ledger's columns."""
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
    for grain in Grain:
        record.update(_grain_cells(computed, grain, computed.measures(grain)))
    return record


def master_columns() -> list[str]:
    """Every column of the ledger, in display order.

    Returns:
        Source columns, then derived context, then the shared measures, then
        each grain's nine-measure family.
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
    """Collapse the ledger to one row per symbol, all three grains.

    The final state of each pool is the last row that touched it, in trade-date
    order.

    Args:
        frame: A ledger.

    Returns:
        One row per symbol carrying units, cost base, average cost and realized
        gain at all three grains, sorted by symbol.
    """
    if frame.empty or "Symbol" not in frame.columns:
        return pd.DataFrame(columns=["Symbol", *_summary_columns()])

    # TxnId is both the index and a column on the ledger, so drop the
    # index to avoid ambiguity for sort_values and groupby.
    tracked = frame[frame["Symbol"].notna()].reset_index(drop=True)
    if tracked.empty:
        return pd.DataFrame(columns=["Symbol", *_summary_columns()])

    records = [
        _summary_record(symbol, rows.iloc[-1])
        for symbol, rows in replay_ordered(tracked).groupby("Symbol", sort=True)
    ]
    return pd.DataFrame(records, columns=["Symbol", *_summary_columns()])


def acb_summary_frames_by_pool(
    frame: pd.DataFrame,
    grain: Grain,
) -> dict[str, pd.DataFrame]:
    """Summarise every pool at one grain in a single pass over the frame.

    Args:
        frame: A ledger.
        grain: The pool grain to split by. Folio grain has a single pool and
            is not a split, so it is rejected.

    Returns:
        Each pool name mapped to the summary frame `acb_summary_frame` would
        have produced for it. Pools whose rows track no symbol are absent.

    Raises:
        ValueError: If asked to split at folio grain.
    """
    if grain is Grain.FOLIO:
        msg = "Folio grain is a single pool; use acb_summary_frame instead."
        raise ValueError(msg)

    column = POOL_COLUMN[grain]
    if frame.empty or "Symbol" not in frame.columns or column not in frame.columns:
        return {}

    # frame where symbols are not missing
    tracked = frame[frame["Symbol"].notna()].reset_index(drop=True)
    if tracked.empty:
        return {}

    grouped: dict[str, list[dict[str, object]]] = {}
    for (pool, symbol), rows in replay_ordered(tracked).groupby(
        [column, "Symbol"],
        sort=True,
    ):
        grouped.setdefault(str(pool), []).append(
            _summary_record(symbol, rows.iloc[-1]),
        )

    columns = ["Symbol", *_summary_columns()]
    return {
        pool: pd.DataFrame(records, columns=columns)
        for pool, records in grouped.items()
    }


_RANK = "_intraday_rank"


def replay_ordered(frame: pd.DataFrame) -> pd.DataFrame:
    """Put ledger rows in the exact order the cost base was replayed in.

    Trade date first, then the day's actions in the order the replay applies
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
    """Read one symbol's closing holding off the last row that touched it."""
    record: dict[str, object] = {"Symbol": symbol}
    for grain in Grain:
        for suffix in ("Units", "ACB", "ACB_USD", "Gain", "Gain_USD"):
            column = grain_column(grain, suffix)
            record[column] = last[column]
        # Both averages are computed rather than carried
        for suffix in ("Avg", "Avg_USD"):
            record[grain_column(grain, suffix)] = _average(
                last[grain_column(grain, "Units")],
                last[grain_column(grain, suffix.replace("Avg", "ACB"))],
            )
    return record


def _summary_columns() -> list[str]:
    """List the per-grain columns the summary frame carries."""
    columns: list[str] = []
    for grain in Grain:
        columns.extend(
            grain_column(grain, suffix)
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
