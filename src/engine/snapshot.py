"""Persist the parts of a replay the master frame does not carry.

The cached frame holds every transaction's computed figures, but a replay
produces three extra things: the diagnostics it raised, the running cash per
pool, and the row counts a report wants to quote. We snapshot these for folio check
to avoid an expensive replay when fingerprints match.

Only the rows a diagnostic actually points at are stored. The diagnostic report only
needs full details for the handful of transactions it is going to print, nothing more.
"""

from __future__ import annotations

from dataclasses import fields
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from engine.events import (
    CashState,
    ComputedRow,
    ReplayResult,
    ReplayTotals,
    ReplayWarning,
    ScopeMeasures,
    TxnRow,
)
from utils.constants import (
    AccountType,
    Action,
    Currency,
    Impact,
    Scope,
    WarningCode,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

# Bump when shape/schema changes below to invalidate the fingerprints.
SCHEMA_VERSION = 1

# Above this many diagnosed rows the snapshot stops being small.
MAX_SNAPSHOT_ROWS = 5_000

# The nine `ScopeMeasures` fields, in declaration order, resolved once.
_MEASURE_FIELDS: tuple[str, ...] = tuple(field.name for field in fields(ScopeMeasures))


def _text(value: Decimal | None) -> str | None:
    """Render a Decimal exactly, keeping None as a genuine blank."""
    return None if value is None else str(value)


def _number(value: Any) -> Decimal | None:  # noqa: ANN401 - reading untyped JSON
    """Read a Decimal back, keeping None as a genuine blank."""
    return None if value is None else Decimal(str(value))


def _measures(measures: ScopeMeasures) -> list[str]:
    """Flatten one scope's nine figures, in declaration order."""
    return [str(getattr(measures, name)) for name in _MEASURE_FIELDS]


def _read_measures(values: Sequence[str]) -> ScopeMeasures:
    """Rebuild one scope's nine figures."""
    return ScopeMeasures(
        **{name: Decimal(values[i]) for i, name in enumerate(_MEASURE_FIELDS)},
    )


def _encode_row(computed: ComputedRow) -> dict[str, Any]:
    """Flatten one computed row, money included, losing nothing."""
    row = computed.row
    return {
        "txn_id": row.txn_id,
        "txn_date": row.txn_date,
        "settle_date": row.settle_date,
        "action": str(row.action),
        "amount": str(row.amount),
        "currency": str(row.currency),
        "price": str(row.price),
        "units": str(row.units),
        "fee": str(row.fee),
        "ticker": row.ticker,
        "account": row.account,
        "description": row.description,
        "symbol": computed.symbol,
        "acct_type": str(computed.acct_type),
        "impact": str(computed.impact),
        "fx_rate": _text(computed.fx_rate),
        "fx_date": computed.fx_date,
        "proceeds_cad": _text(computed.proceeds_cad),
        "proceeds_usd": _text(computed.proceeds_usd),
        "dividend_cad": _text(computed.dividend_cad),
        "dividend_usd": _text(computed.dividend_usd),
        "acct": _measures(computed.acct),
        "type": _measures(computed.type),
        "folio": _measures(computed.folio),
        "flags": [str(code) for code in computed.flags],
    }


def _decode_row(payload: dict[str, Any]) -> ComputedRow:
    """Rebuild one computed row exactly as the replay produced it."""
    return ComputedRow(
        row=TxnRow(
            txn_id=int(payload["txn_id"]),
            txn_date=payload["txn_date"],
            settle_date=payload["settle_date"],
            action=Action(payload["action"]),
            amount=Decimal(payload["amount"]),
            currency=Currency(payload["currency"]),
            price=Decimal(payload["price"]),
            units=Decimal(payload["units"]),
            fee=Decimal(payload["fee"]),
            ticker=payload["ticker"],
            account=payload["account"],
            description=payload["description"],
        ),
        symbol=payload["symbol"],
        acct_type=AccountType(payload["acct_type"]),
        impact=Impact(payload["impact"]),
        fx_rate=_number(payload["fx_rate"]),
        fx_date=payload["fx_date"],
        proceeds_cad=_number(payload["proceeds_cad"]),
        proceeds_usd=_number(payload["proceeds_usd"]),
        dividend_cad=_number(payload["dividend_cad"]),
        dividend_usd=_number(payload["dividend_usd"]),
        acct=_read_measures(payload["acct"]),
        type=_read_measures(payload["type"]),
        folio=_read_measures(payload["folio"]),
        flags=tuple(WarningCode(code) for code in payload["flags"]),
    )


def _encode_warning(warning: ReplayWarning) -> dict[str, Any]:
    """Flatten one diagnostic."""
    return {
        "code": str(warning.code),
        "txn_id": warning.txn_id,
        "scope": str(warning.scope) if warning.scope else None,
        "pool": warning.pool,
        "detail": warning.detail,
        "account": warning.account,
        "symbol": warning.symbol,
        "currency": str(warning.currency) if warning.currency else None,
        "as_of": warning.as_of,
        "value": _text(warning.value),
    }


def _decode_warning(payload: dict[str, Any]) -> ReplayWarning:
    """Rebuild one diagnostic."""
    return ReplayWarning(
        code=WarningCode(payload["code"]),
        txn_id=payload["txn_id"],
        scope=Scope(payload["scope"]) if payload["scope"] else None,
        pool=payload["pool"],
        detail=payload["detail"],
        account=payload["account"],
        symbol=payload["symbol"],
        currency=Currency(payload["currency"]) if payload["currency"] else None,
        as_of=payload["as_of"],
        value=_number(payload["value"]),
    )


def encode(result: ReplayResult) -> dict[str, Any] | None:
    """Flatten everything about a replay the master frame does not already hold.

    Args:
        result: A completed replay.

    Returns:
        A JSON-ready snapshot, or None when the replay diagnosed more rows than
        worth storing.
    """
    # unique txn ids that were diagnosed.
    diagnosed = {
        warning.txn_id for warning in result.warnings if warning.txn_id is not None
    }
    if len(diagnosed) > MAX_SNAPSHOT_ROWS:
        return None

    return {
        "schema": SCHEMA_VERSION,
        "warnings": [_encode_warning(warning) for warning in result.warnings],
        "cash": [
            {
                "scope": str(scope),
                "pool": pool,
                "currency": str(currency),
                **{
                    field.name: str(getattr(state, field.name))
                    for field in fields(CashState)
                },
            }
            for (scope, pool, currency), state in result.cash.items()
        ],
        "totals": [
            [str(action), account, symbol, str(currency), count]
            for (action, account, symbol, currency), count in result.totals.rows.items()
        ],
        "rows": [
            _encode_row(computed)
            for computed in result.rows
            if computed.row.txn_id in diagnosed
        ],
    }


def decode(payload: dict[str, Any]) -> ReplayResult | None:
    """Rebuild a replay result from a snapshot.

    The `rows` carry only the transactions a diagnostic points at. That is all a
    report needs, and it is why a snapshot stays small, but it does mean this
    result is **not** a substitute for a full replay, and nothing that walks
    every row should be handed one.

    Args:
        payload: A snapshot from `encode`.

    Returns:
        The rebuilt result, or None when the snapshot was written by a different
        version of this module and cannot be trusted.
    """
    # schema version shortcircuits decoding.
    if payload.get("schema") != SCHEMA_VERSION:
        return None

    result = ReplayResult(
        rows=[_decode_row(row) for row in payload["rows"]],
        warnings=[_decode_warning(warning) for warning in payload["warnings"]],
    )
    for entry in payload["cash"]:
        key = (Scope(entry["scope"]), entry["pool"], Currency(entry["currency"]))
        result.cash[key] = CashState(
            **{field.name: Decimal(entry[field.name]) for field in fields(CashState)},
        )
    totals = ReplayTotals()
    for action, account, symbol, currency, count in payload["totals"]:
        totals.rows[(Action(action), account, symbol, Currency(currency))] = count
    result.totals = totals
    return result
