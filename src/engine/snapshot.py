"""Persist the parts of a replay the ledger frame does not carry.

The ledger frame holds every transaction's figures, but a replay also raises
diagnostics, keeps the running cash per pool and counts rows by shape. These are
saved beside the frame, so a reused ledger reports exactly what a fresh replay
would without replaying.

Diagnostics are saved as a frame of one record per diagnostic, each carrying the
exact computed row it tags. Only the rows a diagnostic points at are stored: the
diagnostic report needs full details for the transactions it prints, nothing
more. Every figure is stored as text, so no Decimal loses a digit.
"""

from __future__ import annotations

from dataclasses import fields
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pandas as pd

from domain import AccountType, Action, Currency, Grain, Impact, WarningCode
from engine.types import (
    CashState,
    ComputedRow,
    GrainMeasures,
    ReplayTotals,
    ReplayWarning,
    TxnRow,
)

if TYPE_CHECKING:
    from collections.abc import Hashable, Mapping, Sequence

    from engine.types import CashKey

# Bump when what is saved, or the coded *meaning* of a saved field, changes: a
# ledger saved in another format is never reused.
SCHEMA_VERSION = 6

# The nine `GrainMeasures` fields, in declaration order, resolved once.
_MEASURE_FIELDS: tuple[str, ...] = tuple(field.name for field in fields(GrainMeasures))

# The three per-grain measure sets a computed row carries, by attribute.
_GRAINS = ("acct", "type", "folio")

_DIAGNOSTIC_COLUMNS = (
    "code",
    "txn_id",
    "grain",
    "pool",
    "detail",
    "account",
    "symbol",
    "currency",
    "as_of",
    "value",
)

# Prefixed, since a diagnostic and its row both name an account, symbol and currency.
_ROW_COLUMNS = tuple(
    f"row_{name}"
    for name in (
        "txn_date",
        "settle_date",
        "action",
        "amount",
        "currency",
        "price",
        "units",
        "fee",
        "ticker",
        "account",
        "description",
        "symbol",
        "acct_type",
        "impact",
        "fx_rate",
        "fx_date",
        "proceeds_cad",
        "proceeds_usd",
        "dividend_cad",
        "dividend_usd",
        *(f"{grain}_{name}" for grain in _GRAINS for name in _MEASURE_FIELDS),
        "flags",
    )
)


def _text(value: object) -> str | None:
    """Render a value exactly as text, keeping None as a genuine blank."""
    return None if value is None else str(value)


def _number(value: Any) -> Decimal | None:  # noqa: ANN401 - reading untyped storage
    """Read a Decimal back, keeping None as a genuine blank."""
    return None if value is None else Decimal(str(value))


def _encode_diagnostic(diagnostic: ReplayWarning) -> dict[str, str | None]:
    """Flatten one diagnostic."""
    return {
        "code": str(diagnostic.code),
        "txn_id": _text(diagnostic.txn_id),
        "grain": _text(diagnostic.grain),
        "pool": diagnostic.pool,
        "detail": diagnostic.detail,
        "account": diagnostic.account,
        "symbol": diagnostic.symbol,
        "currency": _text(diagnostic.currency),
        "as_of": diagnostic.as_of,
        "value": _text(diagnostic.value),
    }


def _decode_diagnostic(record: Mapping[Hashable, Any]) -> ReplayWarning:
    """Rebuild one diagnostic."""
    return ReplayWarning(
        code=WarningCode(record["code"]),
        txn_id=None if record["txn_id"] is None else int(record["txn_id"]),
        grain=Grain(record["grain"]) if record["grain"] else None,
        pool=record["pool"],
        detail=record["detail"],
        account=record["account"],
        symbol=record["symbol"],
        currency=Currency(record["currency"]) if record["currency"] else None,
        as_of=record["as_of"],
        value=_number(record["value"]),
    )


def _encode_row(computed: ComputedRow) -> dict[str, str | None]:
    """Flatten one computed row, money included, losing nothing."""
    row = computed.row
    values = [
        row.txn_date,
        row.settle_date,
        row.action,
        row.amount,
        row.currency,
        row.price,
        row.units,
        row.fee,
        row.ticker,
        row.account,
        row.description,
        computed.symbol,
        computed.acct_type,
        computed.impact,
        computed.fx_rate,
        computed.fx_date,
        computed.proceeds_cad,
        computed.proceeds_usd,
        computed.dividend_cad,
        computed.dividend_usd,
        *(
            getattr(getattr(computed, grain), name)
            for grain in _GRAINS
            for name in _MEASURE_FIELDS
        ),
        ",".join(str(code) for code in computed.flags),
    ]
    return dict(zip(_ROW_COLUMNS, map(_text, values), strict=True))


def _read_measures(record: Mapping[Hashable, Any], grain: str) -> GrainMeasures:
    """Rebuild one grain's nine figures."""
    return GrainMeasures(
        **{name: Decimal(record[f"row_{grain}_{name}"]) for name in _MEASURE_FIELDS},
    )


def _decode_row(txn_id: int, record: Mapping[Hashable, Any]) -> ComputedRow:
    """Rebuild one computed row exactly as the replay produced it."""
    flags = record["row_flags"]
    return ComputedRow(
        row=TxnRow(
            txn_id=txn_id,
            txn_date=record["row_txn_date"],
            settle_date=record["row_settle_date"],
            action=Action(record["row_action"]),
            amount=Decimal(record["row_amount"]),
            currency=Currency(record["row_currency"]),
            price=Decimal(record["row_price"]),
            units=Decimal(record["row_units"]),
            fee=Decimal(record["row_fee"]),
            ticker=record["row_ticker"],
            account=record["row_account"],
            description=record["row_description"],
        ),
        symbol=record["row_symbol"],
        acct_type=AccountType(record["row_acct_type"]),
        impact=Impact(record["row_impact"]),
        fx_rate=_number(record["row_fx_rate"]),
        fx_date=record["row_fx_date"],
        proceeds_cad=_number(record["row_proceeds_cad"]),
        proceeds_usd=_number(record["row_proceeds_usd"]),
        dividend_cad=_number(record["row_dividend_cad"]),
        dividend_usd=_number(record["row_dividend_usd"]),
        acct=_read_measures(record, "acct"),
        type=_read_measures(record, "type"),
        folio=_read_measures(record, "folio"),
        flags=tuple(WarningCode(code) for code in flags.split(",")) if flags else (),
    )


def encode_diagnostics(
    diagnostics: Sequence[ReplayWarning],
    rows: Mapping[int, ComputedRow],
) -> pd.DataFrame:
    """Lay out diagnostics with the rows they tag, ready to save as Parquet.

    Args:
        diagnostics: Every diagnostic a replay raised, in the order raised.
        rows: The computed row of each transaction a diagnostic tags, by TxnId.

    Returns:
        One text record per diagnostic. Its row's columns are filled on the
        first diagnostic to tag that transaction and blank on the rest, as on a
        diagnostic tagging no transaction.
    """
    blank = dict.fromkeys(_ROW_COLUMNS)
    written: set[int] = set()
    records = []
    for diagnostic in diagnostics:
        txn_id = diagnostic.txn_id
        computed = None if txn_id is None or txn_id in written else rows.get(txn_id)
        row = blank
        if computed is not None:
            row = _encode_row(computed)
            written.add(computed.row.txn_id)
        records.append(_encode_diagnostic(diagnostic) | row)
    return pd.DataFrame(
        records,
        columns=[*_DIAGNOSTIC_COLUMNS, *_ROW_COLUMNS],
        dtype=object,
    )


def decode_diagnostics(
    frame: pd.DataFrame,
) -> tuple[list[ReplayWarning], dict[int, ComputedRow]]:
    """Rebuild the diagnostics and the rows they tag from `encode_diagnostics`.

    Args:
        frame: The saved records.

    Returns:
        The diagnostics in the order raised, and each tagged row by TxnId.
    """
    diagnostics: list[ReplayWarning] = []
    rows: dict[int, ComputedRow] = {}
    for record in frame.to_dict("records"):
        diagnostic = _decode_diagnostic(record)
        diagnostics.append(diagnostic)
        txn_id = diagnostic.txn_id
        if txn_id is not None and record["row_action"]:
            rows[txn_id] = _decode_row(txn_id, record)
    return diagnostics, rows


def encode_cash_and_totals(
    cash: Mapping[CashKey, CashState],
    totals: ReplayTotals,
) -> dict[str, Any]:
    """Flatten each pool's cash and the row counts into JSON-ready lists.

    Args:
        cash: The running cash per pool and currency.
        totals: The row counts by shape.

    Returns:
        A `cash` and a `totals` entry, every figure as text.
    """
    return {
        "cash": [
            {
                "grain": str(grain),
                "pool": pool,
                "currency": str(currency),
                **{
                    field.name: str(getattr(state, field.name))
                    for field in fields(CashState)
                },
            }
            for (grain, pool, currency), state in cash.items()
        ],
        "totals": [
            [str(action), account, symbol, str(currency), count]
            for (action, account, symbol, currency), count in totals.rows.items()
        ],
    }


def decode_cash_and_totals(
    payload: Mapping[str, Any],
) -> tuple[dict[CashKey, CashState], ReplayTotals]:
    """Rebuild each pool's cash and the row counts from `encode_cash_and_totals`.

    Args:
        payload: The saved `cash` and `totals` entries.

    Returns:
        The cash per pool and currency, and the row counts.
    """
    cash = {
        (Grain(entry["grain"]), entry["pool"], Currency(entry["currency"])): CashState(
            **{field.name: Decimal(entry[field.name]) for field in fields(CashState)},
        )
        for entry in payload["cash"]
    }
    totals = ReplayTotals()
    for action, account, symbol, currency, count in payload["totals"]:
        totals.rows[(Action(action), account, symbol, Currency(currency))] = count
    return cash, totals
