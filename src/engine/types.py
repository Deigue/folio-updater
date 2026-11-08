"""Value types the cost-base replay consumes and produces.

The replay reads `Txn`s, whose money is already Decimal: nothing downstream touches
a float.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from db.queries import get_connection, get_rows
from domain import (
    AccountType,
    Action,
    Currency,
    DiagnosticCode,
    Grain,
    Impact,
    Table,
)
from domain.numeric import ZERO
from models import Txn

if TYPE_CHECKING:
    from collections.abc import Collection, Container
    from decimal import Decimal


@dataclass
class HoldingState:
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
    realized_cad: Decimal = ZERO
    realized_usd: Decimal = ZERO
    currency: Currency | None = None
    last_held: str | None = None


@dataclass
class CashState:
    """Running cash and income totals for one pool, in one currency.

    Contributions and withdrawals are tracked apart from cash because
    contribution room is reported from them, and only `CONTRIBUTION` and
    `WITHDRAWAL` count. `TFR_IN` moves cash without consuming room.

    `transfers` is tracked similarly: a `TFR_IN`/`TFR_OUT` leg moves the holder's own
    money between accounts they already own without touching room, so it needs its own
    bucket rather than being folded into `contributions`/`withdrawals` It holds the
    **deposits** a transfer carried, not the value it moved. Those
    differ whenever a pool has grown, and we need this for net deposit tracking.

    `transfers_value` is the same movement measured the other way: what actually
    left or arrived, cash at its face amount and securities at the cost base they
    carried. It exists only to inform reader what exactly got transferred as they
    might see from the broker's perspective.

    `realized_gain` is denominated in this state's own currency, matching every
    other field here. The CAD-converted figure lives on the ledger.
    """

    cash: Decimal = ZERO
    contributions: Decimal = ZERO
    withdrawals: Decimal = ZERO
    transfers: Decimal = ZERO
    transfers_value: Decimal = ZERO
    dividends: Decimal = ZERO
    fees: Decimal = ZERO
    realized_gain: Decimal = ZERO


@dataclass(frozen=True)
class GrainMeasures:
    """One pool's figures after a row has been applied.

    Attributes:
        units: Units held after the row.
        acb_cad: Cost base in CAD after the row.
        acb_usd: Cost base in the holding's own currency, USD holdings only.
        delta_cad: Change in `acb_cad` this row caused.
        delta_usd: Change in `acb_usd` this row caused.
        avg_cad: Average cost in CAD.
        avg_usd: Average cost in USD, USD holdings only.
        realized_cad: Cumulative realized gain in CAD.
        realized_usd: Cumulative realized gain in USD, USD holdings only.
        gain_cad: Realized gain this row earned in CAD. A BUY's is added at valuation.
        gain_usd: The same in USD, USD holdings only.
    """

    units: Decimal = ZERO
    acb_cad: Decimal = ZERO
    acb_usd: Decimal = ZERO
    delta_cad: Decimal = ZERO
    delta_usd: Decimal = ZERO
    avg_cad: Decimal = ZERO
    avg_usd: Decimal = ZERO
    realized_cad: Decimal = ZERO
    realized_usd: Decimal = ZERO
    gain_cad: Decimal = ZERO
    gain_usd: Decimal = ZERO


@dataclass(frozen=True)
class LedgerRow:
    """One ledger row: the source transaction plus every derived measure.

    Strictly one of these per `Txns` row, so the ledger is 1:1 with the
    table it came from.
    """

    row: Txn
    symbol: str | None
    acct_type: AccountType
    impact: Impact
    fx_rate: Decimal | None
    fx_date: str | None
    proceeds_cad: Decimal | None = None
    proceeds_usd: Decimal | None = None
    dividend_cad: Decimal | None = None
    dividend_usd: Decimal | None = None
    acct: GrainMeasures = field(default_factory=GrainMeasures)
    type: GrainMeasures = field(default_factory=GrainMeasures)
    folio: GrainMeasures = field(default_factory=GrainMeasures)
    flags: tuple[DiagnosticCode, ...] = ()

    @property
    def is_usd(self) -> bool:
        """Whether the `_USD` measures on this row carry anything."""
        return self.row.currency is Currency.USD

    def measures(self, grain: Grain) -> GrainMeasures:
        """Return the measures for one pool grain.

        Args:
            grain: Which pool to read.

        Returns:
            That grain's `GrainMeasures`.
        """
        if grain is Grain.ACCOUNT:
            return self.acct
        if grain is Grain.TYPE:
            return self.type
        return self.folio


@dataclass(frozen=True)
class Diagnostic:
    """A diagnostic raised during a replay.

    Most codes tag a row, but few describe a pool rather than a transaction and
    so carry no `txn_id`.

    Attributes:
        code: Which diagnostic fired.
        txn_id: The row it tags, or None for a pool-level diagnostic.
        grain: The pool grain it was detected at, where that matters.
        pool: The account, type or folio key it was detected on.
        detail: Human-readable specifics.
        account: The account the diagnostic is about, where it is about one.
        symbol: The canonical security the diagnostic is about, where there is one.
        currency: The currency the diagnostic is about, for the cash diagnostics.
        as_of: The date the diagnostic is about, `YYYY-MM-DD`, where a row's own
            date is not the answer.
        value: The value the diagnostic turns on: a cash balance for
            `CASH_NEGATIVE`, a unit count for `NEGATIVE_FINAL_UNITS`.
    """

    code: DiagnosticCode
    txn_id: int | None = None
    grain: Grain | None = None
    pool: str | None = None
    detail: str = ""
    account: str | None = None
    symbol: str | None = None
    currency: Currency | None = None
    as_of: str | None = None
    value: Decimal | None = None


# Cash is tracked per pool grain, per pool, per currency. The grain is part of
# the key because an account may legitimately be named after a type.
CashKey = tuple[Grain, str, Currency]

# Rows with shape: action, account, security and currency.
TallyKey = tuple[Action, str, str, Currency]


@dataclass
class ReplayTotals:
    """Row counts by shape. Easy to cache for diagnostics.

    Attributes:
        rows: How many rows carry each `(action, account, symbol, currency)`.
    """

    rows: dict[TallyKey, int] = field(default_factory=dict)

    def add(self, key: TallyKey) -> None:
        """Count one row against its shape."""
        self.rows[key] = self.rows.get(key, 0) + 1

    # * Shortcut for all accounts.
    def accounts(self) -> set[str]:
        """Every account named by any transaction."""
        return {account for _action, account, _symbol, _currency in self.rows}

    def counting(
        self,
        actions: Collection[Action],
        ignored_accounts: Container[str] = (),
        ignored_symbols: Container[str] = (),
    ) -> int:
        """Count rows carrying one of these actions.

        Args:
            actions: The actions to count.
            ignored_accounts: Upper-cased account names to leave out.
            ignored_symbols: Upper-cased symbols to leave out.

        Returns:
            How many rows matched.
        """
        return sum(
            count
            for (action, account, symbol, _currency), count in self.rows.items()
            if action in actions
            and account.upper() not in ignored_accounts
            and symbol.upper() not in ignored_symbols
        )

    def currencies_for(
        self,
        symbol: str,
        actions: Collection[Action],
    ) -> dict[Currency, int]:
        """Count how one security's rows divide across currencies.

        Args:
            symbol: The canonical security.
            actions: Which actions count, only those whose `$` denominates
                money that actually moved.

        Returns:
            Each currency mapped to how many of that security's rows use it.
        """
        tally: dict[Currency, int] = {}
        for (action, _account, held, currency), count in self.rows.items():
            if held == symbol and action in actions:
                tally[currency] = tally.get(currency, 0) + count
        return tally


@dataclass
class ReplayResult:
    """Everything one replay produced.

    Attributes:
        rows: One `LedgerRow` per transaction, in transaction-date order.
        diagnostics: Diagnostics raised, deduplicated.
        cash: Running cash and income totals per pool grain, pool and currency.
        totals: Row counts by shape, so aggregates need no further scan.
    """

    rows: list[LedgerRow] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    cash: dict[CashKey, CashState] = field(default_factory=dict)
    totals: ReplayTotals = field(default_factory=ReplayTotals)

    def codes_by_txn(self) -> dict[int, tuple[DiagnosticCode, ...]]:
        """Group the distinct codes raised against each row.

        Returns:
            Each tagged `TxnId` mapped to its codes, in the order they were
            raised. Rows with no diagnostics are absent.
        """
        grouped: dict[int, dict[DiagnosticCode, None]] = {}
        for diagnostic in self.diagnostics:
            if diagnostic.txn_id is not None:
                grouped.setdefault(diagnostic.txn_id, {}).setdefault(
                    diagnostic.code,
                    None,
                )
        return {txn_id: tuple(codes) for txn_id, codes in grouped.items()}


def load_txns() -> list[Txn]:
    """Read the whole transactions table as `Txn`s.

    Returns:
        Every transaction in the folio, ordered by transaction date then TxnId.
    """
    with get_connection() as conn:
        frame = get_rows(conn, Table.TXNS)
    records = frame.to_dict("records")
    # A frame's records are keyed by column name.
    txns = [Txn.from_row(record) for record in records]  # ty: ignore[invalid-argument-type]
    txns.sort(key=lambda txn: (txn.txn_date, txn.txn_id))
    return txns
