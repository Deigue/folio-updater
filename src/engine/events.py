"""Value types the cost-base replay consumes and produces.

`TxnRow` is where a raw `Txns` row becomes Decimal: nothing downstream touches a float.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from db.queries import get_connection, get_rows
from utils.constants import (
    AccountType,
    Action,
    Column,
    Currency,
    Impact,
    Scope,
    Table,
    WarningCode,
)
from utils.numeric import ZERO, dec

if TYPE_CHECKING:
    from decimal import Decimal

    import pandas as pd

# What each action does to the cost base. Anything absent is cash-only.
ACTION_IMPACT: dict[Action, Impact] = {
    Action.BUY: Impact.ACB,
    Action.SELL: Impact.ACB,
    Action.ROC: Impact.ACB,
    Action.SPLIT: Impact.ACB,
    Action.TFR_IN: Impact.ACB,
    Action.TFR_OUT: Impact.ACB,
    Action.DIVIDEND: Impact.INCOME,
    Action.FCH: Impact.INCOME,
    Action.CONTRIBUTION: Impact.NONE,
    Action.WITHDRAWAL: Impact.NONE,
    Action.FXT: Impact.NONE,
}


@dataclass(frozen=True)
class TxnRow:
    """One `Txns` row with every money column as a Decimal.

    Attributes:
        txn_id: Primary key.
        txn_date: Trade date, `YYYY-MM-DD`. Units and cost base follow this.
        settle_date: Settlement date, falling back to the trade date. Cash
            follows this.
        action: The transaction's action.
        amount: Total cash amount, sign as stored.
        currency: Currency the row is denominated in.
        price: Price per unit; shares *before* the split on a SPLIT row.
        units: Number of units; shares *after* the split on a SPLIT row.
        fee: Commission. Which sign means "charged" varies by broker, so it is detected
            per account rather than assumed.
        ticker: Security symbol, as written on the row.
        account: Owning account name.
        description: Free-text description when the optional column exists.
    """

    txn_id: int
    txn_date: str
    settle_date: str
    action: Action
    amount: Decimal
    currency: Currency
    price: Decimal
    units: Decimal
    fee: Decimal
    ticker: str | None
    account: str
    description: str | None = None

    @property
    def fx_date(self) -> str:
        """Date whose rate converts this row: the settle date where there is one."""
        return self.settle_date or self.txn_date

    @property
    def impact(self) -> Impact:
        """What this row does to the cost base."""
        return ACTION_IMPACT.get(self.action, Impact.NONE)

    @property
    def is_position_transfer(self) -> bool:
        """Whether a transfer leg moves units rather than cash."""
        return bool(self.ticker) and self.units != ZERO


@dataclass
class PositionState:
    """Running units, cost base and realized gain for one pool and symbol.

    `acb_usd` stays at zero for CAD-denominated holdings

    `last_held` is carried only so income arriving just after a sale can be told
    apart from income against a pool that never held the symbol. It is the trade
    date of the most recent row that left units outstanding, and stays None for
    a pool that has never held any.
    """

    units: Decimal = ZERO
    acb_cad: Decimal = ZERO
    acb_usd: Decimal = ZERO
    gain_cad: Decimal = ZERO
    gain_usd: Decimal = ZERO
    currency: Currency | None = None
    last_held: str | None = None


@dataclass
class CashState:
    """Running cash and income totals for one pool, in one currency.

    Contributions and withdrawals are tracked apart from cash because
    contribution room is reported from them, and only `CONTRIBUTION` and
    `WITHDRAWAL` count. `TFR_IN` moves cash without consuming room.

    `realized_gain` is denominated in this state's own currency, matching every
    other field here. The CAD-converted figure lives on the master frame.
    """

    cash: Decimal = ZERO
    contributions: Decimal = ZERO
    withdrawals: Decimal = ZERO
    dividends: Decimal = ZERO
    fees: Decimal = ZERO
    realized_gain: Decimal = ZERO


@dataclass(frozen=True)
class ScopeMeasures:
    """One pool's figures after a row has been applied.

    Attributes:
        units: Units held after the row.
        acb_cad: Cost base in CAD after the row.
        acb_usd: Cost base in the holding's own currency, USD holdings only.
        delta_cad: Change in `acb_cad` this row caused.
        delta_usd: Change in `acb_usd` this row caused.
        avg_cad: Cost base per share in CAD.
        avg_usd: Cost base per share in USD, USD holdings only.
        gain_cad: Cumulative realized gain in CAD.
        gain_usd: Cumulative realized gain in USD, USD holdings only.
    """

    units: Decimal = ZERO
    acb_cad: Decimal = ZERO
    acb_usd: Decimal = ZERO
    delta_cad: Decimal = ZERO
    delta_usd: Decimal = ZERO
    avg_cad: Decimal = ZERO
    avg_usd: Decimal = ZERO
    gain_cad: Decimal = ZERO
    gain_usd: Decimal = ZERO


@dataclass(frozen=True)
class ComputedRow:
    """One master row: the source transaction plus every derived measure.

    Strictly one of these per `Txns` row, so the master frame is 1:1 with the
    table it came from.
    """

    row: TxnRow
    symbol: str | None
    acct_type: AccountType
    impact: Impact
    fx_rate: Decimal | None
    fx_date: str | None
    proceeds_cad: Decimal | None = None
    proceeds_usd: Decimal | None = None
    dividend_cad: Decimal | None = None
    dividend_usd: Decimal | None = None
    acct: ScopeMeasures = field(default_factory=ScopeMeasures)
    type: ScopeMeasures = field(default_factory=ScopeMeasures)
    folio: ScopeMeasures = field(default_factory=ScopeMeasures)
    flags: tuple[WarningCode, ...] = ()

    @property
    def is_usd(self) -> bool:
        """Whether the `_USD` measures on this row carry anything."""
        return self.row.currency is Currency.USD

    def measures(self, scope: Scope) -> ScopeMeasures:
        """Return the measures for one pool grain.

        Args:
            scope: Which pool to read.

        Returns:
            That scope's `ScopeMeasures`.
        """
        if scope is Scope.ACCOUNT:
            return self.acct
        if scope is Scope.TYPE:
            return self.type
        return self.folio


@dataclass(frozen=True)
class ReplayWarning:
    """A diagnostic raised during a replay.

    Most codes tag a row, but `UNKNOWN_ACCOUNT_TYPE` and
    `UNRECORDED_CASH_TRANSFER` are account-level and carry no `txn_id`.

    Attributes:
        code: Which diagnostic fired.
        txn_id: The row it tags, or None for an account-level finding.
        scope: The pool grain it was detected at, where that matters.
        pool: The account, type or portfolio key it was detected on.
        detail: Human-readable specifics.
    """

    code: WarningCode
    txn_id: int | None = None
    scope: Scope | None = None
    pool: str | None = None
    detail: str = ""


# Cash is tracked per pool grain, per pool, per currency. The scope is part of
# the key because an account may legitimately be named after a type.
CashKey = tuple[Scope, str, Currency]


@dataclass
class ReplayResult:
    """Everything one replay produced.

    Attributes:
        rows: One `ComputedRow` per transaction, in trade-date order.
        warnings: Diagnostics raised, deduplicated.
        cash: Running cash and income totals per pool grain, pool and currency.
    """

    rows: list[ComputedRow] = field(default_factory=list)
    warnings: list[ReplayWarning] = field(default_factory=list)
    cash: dict[CashKey, CashState] = field(default_factory=dict)

    def codes_for(self, txn_id: int) -> tuple[WarningCode, ...]:
        """Return the distinct codes raised against one row.

        Args:
            txn_id: The row to look up.

        Returns:
            The codes tagging that row, in the order they were raised.
        """
        seen: dict[WarningCode, None] = {}
        for warning in self.warnings:
            if warning.txn_id == txn_id:
                seen.setdefault(warning.code, None)
        return tuple(seen)


def _currency(value: object) -> Currency:
    """Read a `$` cell, defaulting to CAD when it is missing or unrecognised."""
    try:
        return Currency(str(value).strip().upper())
    except ValueError:
        return Currency.CAD


def _text(value: object) -> str | None:
    """Read a nullable text cell, treating pandas NaN sentinels as absent."""
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text.lower() in {"nan", "none", "<na>"} else text


def _action(value: object) -> Action | None:
    """Read an `Action` cell, or None when it is not one the engine knows."""
    try:
        return Action(str(value).strip().upper())
    except ValueError:
        return None


def to_txn_rows(frame: pd.DataFrame) -> list[TxnRow]:
    """Convert raw transaction rows into `TxnRow`s.

    Applies decimal conversion and type validation.
    Rows carrying an action the engine does not know are dropped.

    Args:
        frame: Rows straight from the `Txns` table.

    Returns:
        `TxnRow`s ordered by trade date then TxnId.
    """
    if frame.empty:
        return []

    rows: list[TxnRow] = []
    records = frame.to_dict("records")
    for record in records:
        action = _action(record.get(Column.Txn.ACTION))
        if action is None:
            continue
        txn_date = str(record.get(Column.Txn.TXN_DATE) or "")
        settle_raw = record.get(Column.Txn.SETTLE_DATE)
        settle_date = str(settle_raw) if settle_raw and str(settle_raw) != "nan" else ""
        ticker = _text(record.get(Column.Txn.TICKER))
        description = _text(record.get("Description"))
        rows.append(
            TxnRow(
                txn_id=int(record[Column.Txn.TXN_ID]),
                txn_date=txn_date,
                settle_date=settle_date or txn_date,
                action=action,
                amount=dec(record.get(Column.Txn.AMOUNT)),
                currency=_currency(record.get(Column.Txn.CURRENCY)),
                price=dec(record.get(Column.Txn.PRICE)),
                units=dec(record.get(Column.Txn.UNITS)),
                fee=dec(record.get(Column.Txn.FEE)),
                ticker=ticker.upper() if ticker else None,
                account=str(record[Column.Txn.ACCOUNT]),
                description=description,
            ),
        )
    rows.sort(key=lambda row: (row.txn_date, row.txn_id))
    return rows


def load_txn_rows() -> list[TxnRow]:
    """Read the whole transactions table as `TxnRow`s.

    Returns:
        Every transaction in the folio, trade-date ordered.
    """
    with get_connection() as conn:
        frame = get_rows(conn, Table.TXNS)
    return to_txn_rows(frame)
