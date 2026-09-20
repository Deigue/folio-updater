"""The cost-base sheets: a ledger, a per-symbol buildup, and a summary.

The ledger is the master sheet: one row per transaction, carrying its account
and account type beside all three pool grains at once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import Column, Scope
from engine.frames import (
    CONTEXT_COLUMNS,
    SCOPE_MEASURES,
    SCOPE_PREFIX,
    SHARED_COLUMNS,
    SOURCE_COLUMNS,
    acb_summary_frame,
    scope_column,
)
from exporters.table import Col, Fmt, Row, Table, row_of

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

COLUMN_FORMATS: dict[str, Fmt] = {
    str(Column.Txn.TXN_ID): Fmt.ID,
    str(Column.Txn.TXN_DATE): Fmt.DATE,
    str(Column.Txn.SETTLE_DATE): Fmt.DATE,
    str(Column.Txn.AMOUNT): Fmt.MONEY_SIGNED,
    str(Column.Txn.PRICE): Fmt.PRICE,
    str(Column.Txn.UNITS): Fmt.UNITS,
    str(Column.Txn.FEE): Fmt.MONEY,
    "FXRate": Fmt.RATE,
    "FXDate": Fmt.DATE,
    "Proceeds": Fmt.MONEY,
    "Proceeds_USD": Fmt.MONEY,
    "Dividend": Fmt.MONEY,
    "Dividend_USD": Fmt.MONEY,
}

# Columns whose zero says nothing at all.
QUIET_COLUMNS = frozenset(
    {
        str(Column.Txn.PRICE),
        str(Column.Txn.UNITS),
        str(Column.Txn.FEE),
        "Proceeds",
        "Proceeds_USD",
        "Dividend",
        "Dividend_USD",
    },
)

# What each pool grain's band of columns is called.
SCOPE_BANDS: dict[Scope, str] = {
    Scope.ACCOUNT: "Account pool",
    Scope.TYPE: "Type pool",
    Scope.FOLIO: "Portfolio",
}

_TRANSACTION_BAND = "Transaction"

LEDGER_ORDER: tuple[str, ...] = (
    "Symbol",
    str(Column.Txn.TXN_DATE),
    str(Column.Txn.ACTION),
    str(Column.Txn.ACCOUNT),
    str(Column.Txn.CURRENCY),
    str(Column.Txn.UNITS),
    str(Column.Txn.PRICE),
    str(Column.Txn.AMOUNT),
    # Everything past here scrolls away.
    str(Column.Txn.TXN_ID),
    str(Column.Txn.SETTLE_DATE),
    # `Ticker` is what the broker wrote; `Symbol` above is what that resolves
    # to today, and what the cost base is pooled under. The two differ only
    # across a rename, which is exactly when knowing both matters.
    str(Column.Txn.TICKER),
    str(Column.Txn.FEE),
    "AcctType",
    "Impact",
    "FXRate",
    "FXDate",
    "Proceeds",
    "Proceeds" + USD,
    "Dividend",
    "Dividend" + USD,
    "Flags",
)

PINNED_COLUMNS = 8

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
    cad, usd = _families(currency, usd=_has_usd(rows, (scope,)))
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
        notes=(*notes, *_notes(rows)),
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
    cad, usd = _families(currency, usd=_has_usd(summary, (scope,)))
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
        notes=(*notes, *_notes(rows)),
    )


def acb_ledger_table(
    frame: pd.DataFrame,
    *,
    name: str = "ACB",
    currency: str = "both",
    notes: Sequence[str] = (),
) -> Table:
    """Lay the whole replay out: one row per transaction, every grain at once.

    Args:
        frame: Rows from the master frame.
        name: What the sheet is called.
        currency: `CAD`, `USD` or `both`.
        notes: Lines to disclose above the ones the table derives itself.

    Returns:
        The table, in transaction order, its columns banded by pool grain.
    """
    cad, usd = _families(currency, usd=_has_usd(frame, tuple(Scope)))
    keys = [
        column for column in _ordered_columns() if _wanted(column, cad=cad, usd=usd)
    ]
    columns = [
        Col(
            _header(key),
            COLUMN_FORMATS.get(key, Fmt.TEXT),
            _TRANSACTION_BAND,
            quiet=key in QUIET_COLUMNS,
        )
        for key in keys
    ]

    for scope in Scope:
        for suffix, _attribute in SCOPE_MEASURES:
            if not _wanted(suffix, cad=cad, usd=usd):
                continue
            keys.append(scope_column(scope, suffix))
            columns.append(
                Col(
                    f"{SCOPE_PREFIX[scope]} {_header(suffix)}",
                    MEASURE_FORMATS[suffix.removesuffix(USD)],
                    SCOPE_BANDS[scope],
                ),
            )

    ordered = frame.sort_values(str(Column.Txn.TXN_ID), kind="stable")
    return Table(
        name=name,
        columns=tuple(columns),
        # Keys and columns are built in step, so the row is already in order.
        rows=tuple(
            Row(values) for values in ordered[keys].itertuples(index=False, name=None)
        ),
        notes=(*notes, *_notes(frame)),
        freeze=PINNED_COLUMNS,
    )


def _ordered_columns() -> list[str]:
    """Order the ledger's transaction columns, keeping every one of them.

    Anything the engine grows that `LEDGER_ORDER` has not been told about lands
    on the end rather than falling off the sheet.
    """
    known = [*SOURCE_COLUMNS, *CONTEXT_COLUMNS, *SHARED_COLUMNS]
    ordered = [column for column in LEDGER_ORDER if column in known]
    return ordered + [column for column in known if column not in LEDGER_ORDER]


def _header(column: str) -> str:
    """Spell a frame column the way a reader should see it."""
    return column.replace(USD, " USD")


def _wanted(column: str, *, cad: bool, usd: bool) -> bool:
    """Report whether a currency's family of columns was asked for."""
    if column.endswith(USD):
        return usd
    if column in {"ACB", "Delta", "Avg", "Gain", "Proceeds", "Dividend"}:
        return cad
    return True


def _families(currency: str, *, usd: bool) -> tuple[bool, bool]:
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


def _has_usd(frame: pd.DataFrame, scopes: Sequence[Scope]) -> bool:
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


def _notes(rows: pd.DataFrame) -> list[str]:
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
