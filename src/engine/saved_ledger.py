"""Persist the parts of a replay the ledger frame does not carry.

The ledger frame holds every transaction's figures, but a replay also raises
diagnostics, keeps the running cash per pool, counts rows by shape and records
each transfer leg's deposit share. These are saved beside the frame, so a reused
ledger reports exactly what a fresh replay would without replaying.

Diagnostics are saved as a frame of one record per diagnostic, each carrying the
exact ledger row it tags. Only the rows a diagnostic points at are stored: the
diagnostic report needs full details for the transactions it prints, nothing
more. Every figure is stored as text, so no Decimal loses a digit.
"""

from __future__ import annotations

import json
from dataclasses import fields
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pandas as pd

from domain import AccountType, Action, Currency, DiagnosticCode, Grain, Impact
from engine.accounts import Pool
from engine.types import (
    CashState,
    DepositShare,
    Diagnostic,
    GrainMeasures,
    LedgerRow,
    ReplayTotals,
)
from models import Txn

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable, Mapping, Sequence

    from engine.types import CashKey

# Bump when what is saved, or the coded *meaning* of a saved field, changes: a
# ledger saved in another format is never reused.
SCHEMA_VERSION = 14

# The `GrainMeasures` fields, in declaration order, resolved once.
_MEASURE_FIELDS: tuple[str, ...] = tuple(field.name for field in fields(GrainMeasures))

# The three per-grain measure sets a ledger row carries, by attribute.
_GRAINS = ("acct", "type", "folio")

# Every column below is read off the dataclasses, so a field added to
# `Diagnostic`, `Txn` or `LedgerRow` is saved with no edit here. Only a
# field that must come back as something other than text needs a decoder, and
# only one that is not text already needs an encoder.
_DIAGNOSTIC_COLUMNS = tuple(field.name for field in fields(Diagnostic))

# The transaction's own columns (its id is the key), then the derived scalars
# of the ledger row beside it. The per-grain measures and the flags follow.
_TXN_FIELDS = tuple(field.name for field in fields(Txn) if field.name != "txn_id")
_DERIVED_FIELDS = tuple(
    field.name
    for field in fields(LedgerRow)
    if field.name not in {"row", "flags", *_GRAINS}
)

# Prefixed, since a diagnostic and its row both name an account, symbol and currency.
_ROW_COLUMNS = tuple(
    f"row_{name}"
    for name in (
        *_TXN_FIELDS,
        *_DERIVED_FIELDS,
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


def _same(value: Any) -> Any:  # noqa: ANN401 - a field read back as it was stored
    """Hand a stored value back unchanged."""
    return value


def _optional(convert: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Wrap a decoder so that a blank stays None."""
    return lambda value: convert(value) if value else None


def _encode_pool(pool: Pool | None) -> str | None:
    """Flatten a pool into text, keeping None as a genuine blank."""
    return None if pool is None else json.dumps([str(pool.grain), pool.name])


def _decode_pool(stored: str) -> Pool:
    """Restore a pool from `_encode_pool`."""
    grain, name = json.loads(stored)
    return Pool(Grain(grain), name)


# How the fields that are not plain text come back from storage.
_DIAGNOSTIC_DECODERS: dict[str, Callable[[Any], Any]] = {
    "code": DiagnosticCode,
    "txn_id": _optional(int),
    "pool": _optional(_decode_pool),
    "currency": _optional(Currency),
    "value": _number,
}
_ROW_DECODERS: dict[str, Callable[[Any], Any]] = {
    "action": Action,
    "amount": _number,
    "currency": Currency,
    "price": _number,
    "units": _number,
    "fee": _number,
    "settle_calculated": lambda value: value == str(True),
    "extras": json.loads,
    "acct_type": AccountType,
    "impact": Impact,
    "fx_rate": _number,
    "proceeds_cad": _number,
    "proceeds_usd": _number,
    "dividend_cad": _number,
    "dividend_usd": _number,
}


# How the transaction fields that are not plain values go into storage.
_ROW_ENCODERS: dict[str, Callable[[Any], Any]] = {
    "extras": lambda extras: json.dumps(dict(extras)),
}


def _encode_diagnostic(diagnostic: Diagnostic) -> dict[str, str | None]:
    """Flatten one diagnostic."""
    encoded = {name: _text(getattr(diagnostic, name)) for name in _DIAGNOSTIC_COLUMNS}
    return encoded | {"pool": _encode_pool(diagnostic.pool)}


def _decode_diagnostic(record: Mapping[Hashable, Any]) -> Diagnostic:
    """Restore one diagnostic."""
    return Diagnostic(
        **{
            name: _DIAGNOSTIC_DECODERS.get(name, _same)(record[name])
            for name in _DIAGNOSTIC_COLUMNS
        },
    )


def _encode_row(computed: LedgerRow) -> dict[str, str | None]:
    """Flatten one ledger row, money included, losing nothing."""
    values = [
        *(
            _ROW_ENCODERS.get(name, _same)(getattr(computed.row, name))
            for name in _TXN_FIELDS
        ),
        *(getattr(computed, name) for name in _DERIVED_FIELDS),
        *(
            getattr(getattr(computed, grain), name)
            for grain in _GRAINS
            for name in _MEASURE_FIELDS
        ),
        ",".join(str(code) for code in computed.flags),
    ]
    return dict(zip(_ROW_COLUMNS, map(_text, values), strict=True))


def _read_measures(record: Mapping[Hashable, Any], grain: str) -> GrainMeasures:
    """Restore one grain's figures."""
    return GrainMeasures(
        **{name: Decimal(record[f"row_{grain}_{name}"]) for name in _MEASURE_FIELDS},
    )


def _decode_row(txn_id: int, record: Mapping[Hashable, Any]) -> LedgerRow:
    """Restore one ledger row exactly as the replay produced it."""
    flags = record["row_flags"]
    stored = {
        name: _ROW_DECODERS.get(name, _same)(record[f"row_{name}"])
        for name in (*_TXN_FIELDS, *_DERIVED_FIELDS)
    }
    return LedgerRow(
        row=Txn(txn_id=txn_id, **{name: stored[name] for name in _TXN_FIELDS}),
        **{name: stored[name] for name in _DERIVED_FIELDS},
        acct=_read_measures(record, "acct"),
        type=_read_measures(record, "type"),
        folio=_read_measures(record, "folio"),
        flags=tuple(DiagnosticCode(code) for code in flags.split(",")) if flags else (),
    )


def encode_diagnostics(
    diagnostics: Sequence[Diagnostic],
    rows: Mapping[int, LedgerRow],
) -> pd.DataFrame:
    """Pair diagnostics with the rows they tag, ready to save as Parquet.

    Args:
        diagnostics: Every diagnostic a replay raised, in the order raised.
        rows: The ledger row of each transaction a diagnostic tags, by TxnId.

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
) -> tuple[list[Diagnostic], dict[int, LedgerRow]]:
    """Restore the diagnostics and the rows they tag from `encode_diagnostics`.

    Args:
        frame: The saved records.

    Returns:
        The diagnostics in the order raised, and each tagged row by TxnId.
    """
    diagnostics: list[Diagnostic] = []
    rows: dict[int, LedgerRow] = {}
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
                "grain": str(pool.grain),
                "pool": pool.name,
                "currency": str(currency),
                **{
                    field.name: str(getattr(state, field.name))
                    for field in fields(CashState)
                },
            }
            for (pool, currency), state in cash.items()
        ],
        "totals": [
            [str(action), account, symbol, str(currency), count]
            for (action, account, symbol, currency), count in totals.rows.items()
        ],
    }


def decode_cash_and_totals(
    payload: Mapping[str, Any],
) -> tuple[dict[CashKey, CashState], ReplayTotals]:
    """Restore each pool's cash and the row counts from `encode_cash_and_totals`.

    Args:
        payload: The saved `cash` and `totals` entries.

    Returns:
        The cash per pool and currency, and the row counts.
    """
    cash = {
        (
            Pool(Grain(entry["grain"]), entry["pool"]),
            Currency(entry["currency"]),
        ): CashState(
            **{field.name: Decimal(entry[field.name]) for field in fields(CashState)},
        )
        for entry in payload["cash"]
    }
    totals = ReplayTotals()
    for action, account, symbol, currency, count in payload["totals"]:
        totals.rows[(Action(action), account, symbol, Currency(currency))] = count
    return cash, totals


def encode_deposit_shares(shares: Sequence[DepositShare]) -> list[list[Any]]:
    """Flatten each leg's deposit share into JSON-ready lists, money as text."""
    return [
        [
            share.txn_id,
            share.settle_date,
            str(share.pool.grain),
            share.pool.name,
            str(share.currency),
            str(share.amount),
        ]
        for share in shares
    ]


def decode_deposit_shares(entries: Sequence[Sequence[Any]]) -> tuple[DepositShare, ...]:
    """Restore the deposit shares from `encode_deposit_shares`."""
    return tuple(
        DepositShare(
            txn_id,
            settle_date,
            Pool(Grain(grain), pool),
            Currency(currency),
            Decimal(amount),
        )
        for txn_id, settle_date, grain, pool, currency, amount in entries
    )
