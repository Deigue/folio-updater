"""The Ledger sheet: every transaction, with its cost base at every pool grain."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import Column, Grain
from engine.frames import (
    CONTEXT_COLUMNS,
    GRAIN_MEASURES,
    GRAIN_PREFIX,
    SHARED_COLUMNS,
    SOURCE_COLUMNS,
    grain_column,
    replay_ordered,
)
from exporters.excel_style import COST_BASE_TAB
from exporters.sheets.acb import MEASURE_FORMATS, USD, acb_notes, families, has_usd
from exporters.sheets.stored import TXN_FORMATS, TXN_QUIET
from exporters.table import Col, Fmt, Row, Table

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pandas as pd

LEDGER_SHEET = "Ledger"

# The stored columns read as they do on the `Txns` sheet; the rest are the
# ledger's own.
COLUMN_FORMATS: dict[str, Fmt] = {
    **TXN_FORMATS,
    str(Column.Txn.TXN_ID): Fmt.ID,
    "FXRate": Fmt.RATE,
    "FXDate": Fmt.DATE,
    "Proceeds": Fmt.MONEY,
    "Proceeds_USD": Fmt.MONEY,
    "Dividend": Fmt.MONEY,
    "Dividend_USD": Fmt.MONEY,
}

# Columns whose zero says nothing at all.
QUIET_COLUMNS = TXN_QUIET | {"Proceeds", "Proceeds_USD", "Dividend", "Dividend_USD"}

# What each pool grain's band of columns is called.
GRAIN_BANDS: dict[Grain, str] = {
    Grain.ACCOUNT: "Account pool",
    Grain.TYPE: "Type pool",
    Grain.FOLIO: "Portfolio",
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
    # The sheet freezes the columns above `TxnId`; everything from there on
    # scrolls away.
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


def ledger_table(
    frame: pd.DataFrame,
    *,
    txns: pd.DataFrame | None = None,
    name: str = LEDGER_SHEET,
    currency: str = "both",
    notes: Sequence[str] = (),
) -> Table:
    """Generate the Ledger: one row per transaction, every grain at once.

    Args:
        frame: Rows from the ledger.
        txns: The stored transactions, whose columns the frame does not carry
            are added to the transaction band.
        name: What the sheet is called.
        currency: `CAD`, `USD` or `both`.
        notes: Lines to disclose above the ones the table derives itself.

    Returns:
        The table, in transaction order, its columns banded by pool grain.
    """
    cad, usd = families(currency, usd=has_usd(frame, tuple(Grain)))
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

    for grain in Grain:
        for suffix, _attribute in GRAIN_MEASURES:
            if not _wanted(suffix, cad=cad, usd=usd):
                continue
            keys.append(grain_column(grain, suffix))
            columns.append(
                Col(
                    f"{GRAIN_PREFIX[grain]} {_header(suffix)}",
                    MEASURE_FORMATS[suffix.removesuffix(USD)],
                    GRAIN_BANDS[grain],
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
        freeze=keys.index(str(Column.Txn.TXN_ID)),
    )


def _with_stored_columns(
    frame: pd.DataFrame,
    txns: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str]]:
    """Join the stored columns the ledger leaves out, keyed on `TxnId`.

    Every stored transaction is listed, so one the replay dropped still is, with
    the columns it was stored with and none of the replay's.

    Returns:
        The joined rows, and the names of the columns the join added.
    """
    txn_id = str(Column.Txn.TXN_ID)
    extra = [
        column
        for column in txns.columns
        if column not in frame.columns and column != txn_id
    ]
    # The frame's figures win; a transaction the replay dropped has none, so
    # it keeps what was stored.
    joined = frame.set_index(txn_id).combine_first(txns.set_index(txn_id)).reset_index()
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
