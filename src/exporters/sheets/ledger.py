"""The Ledger sheet: every transaction, with its cost base at every pool grain."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import Column, Scope
from engine.frames import (
    CONTEXT_COLUMNS,
    SCOPE_MEASURES,
    SCOPE_PREFIX,
    SHARED_COLUMNS,
    SOURCE_COLUMNS,
    replay_ordered,
    scope_column,
)
from exporters.excel_style import COST_BASE_TAB
from exporters.sheets.acb import MEASURE_FORMATS, USD, acb_notes, families, has_usd
from exporters.table import Col, Fmt, Row, Table

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pandas as pd

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


def ledger_table(
    frame: pd.DataFrame,
    *,
    txns: pd.DataFrame | None = None,
    name: str = "Ledger",
    currency: str = "both",
    notes: Sequence[str] = (),
) -> Table:
    """Generate the Ledger: one row per transaction, every grain at once.

    Args:
        frame: Rows from the master frame.
        txns: The stored transactions, whose columns the frame does not carry
            are added to the transaction band.
        name: What the sheet is called.
        currency: `CAD`, `USD` or `both`.
        notes: Lines to disclose above the ones the table derives itself.

    Returns:
        The table, in transaction order, its columns banded by pool grain.
    """
    cad, usd = families(currency, usd=has_usd(frame, tuple(Scope)))
    extra: list[str] = []
    if txns is not None:
        frame, extra = _with_stored_columns(frame, txns)
    keys = [
        column for column in _ordered_columns() if _wanted(column, cad=cad, usd=usd)
    ]
    keys.extend(extra)
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

    # Earliest first, in the order the replay applied them, so every running
    # column reads down the page as the accumulation it is.
    ordered = replay_ordered(frame)
    return Table(
        name=name,
        columns=tuple(columns),
        # Keys and columns are built in step, so the row is already in order.
        rows=tuple(
            Row(values) for values in ordered[keys].itertuples(index=False, name=None)
        ),
        notes=(*notes, *acb_notes(frame)),
        tab_color=COST_BASE_TAB,
        freeze=PINNED_COLUMNS,
    )


def _with_stored_columns(
    frame: pd.DataFrame,
    txns: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str]]:
    """Join the stored columns the master frame leaves out, keyed on `TxnId`.

    The stored table is the left side, so a transaction the replay dropped is
    still listed.

    Returns:
        The joined rows, and the names of the columns the join added.
    """
    txn_id = str(Column.Txn.TXN_ID)
    extra = [
        column
        for column in txns.columns
        if column not in frame.columns and column != txn_id
    ]
    joined = txns[[txn_id, *extra]].merge(
        frame.reset_index(drop=True),
        on=txn_id,
        how="left",
    )
    return joined, extra


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
