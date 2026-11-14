"""Track how much of a pool's own money a transfer carries with it.

Net deposits tracks "how much of the holder's own money is in here", so a
transfer between two accounts the holder already owns must move the **deposit**
portion of what it carries, and nothing else. This is more complex to calculate
accurately than how brokers calculate:

- **Face value** moves too much. Sell a holding at a gain and sweep the cash
  to another broker, then taking face value means you are counting gains as part of the
  pools "deposit"
- **Cost base** moves the wrong thing. A dividend reinvested at the old broker
  raises the cost base without any new money arriving, so ACB books that growth
  as a deposit too.

The rule instead is proportional. A transfer that removes `value_leaving` from a pool
with a pool basis (cost base plus cash) of `basis_cad`, then the **real** moved is:

    f       = value_leaving / basis_cad
    moved   = f * deposits

`basis_cad` is **cost base plus cash**, not market value.
For a pool holding X (cost 5k, worth 5k) and Y (cost 5k, worth 10k) with 10k of deposits
moving only Y takes 5k of deposits on pool basis and 6.67k on market basis: pool basis
leaves Y's gain attached to Y at its new account, where it was earned, instead of
smearing it across both.

Two properties make this safe to rely on:

- **Both legs move the same vector.** The out leg computes it, the in leg
  applies it unchanged, so nothing is created or destroyed in transit.
- **`deposits / basis_cad` is invariant under a transfer.** Several transfers out
  of one pool therefore produce the same result in any order, as long as each out
  leg comes before its in leg, which is what lets a full account transfer land
  exactly on the contributions that funded it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from domain import Action, Currency, Grain
from domain.numeric import HALF_CENT, ONE, ZERO
from engine.accounts import Pool
from engine.transfers import TRANSFER_ACTIONS
from engine.types import DepositShare, TransferTotals

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from engine.fx_rates import FxRates
    from engine.transfers import Crossing, TransferPair
    from engine.types import CashEffect, CashKey, LedgerRow
    from models import Txn

# An amount per currency. Never blended: a contribution is denominated in the currency
# it was made in, whatever it was later invested in.
_PerCurrency = dict[Currency, Decimal]

# A pool emptied by several legs divides by a slightly smaller pool basis each time,
# and fractions do not recombine exactly, so a fully transferred account can be left
# holding a millionth of a cent of deposits.
DEPOSIT_DUST = HALF_CENT


def fraction_leaving(value_leaving: Decimal, basis_cad: Decimal) -> Decimal:
    """Return what share of a pool a leg removes, clamped to a usable range.

    Guards against these scenarios:

    - **Empty pool.** Dividing by a zero pool basis is undefined, and nothing of
      value is leaving, so nothing is taken. Deposits may still be non-zero
      here (contribute, then lose it all to fees), in which case they stay
      stranded in the empty pool rather than moving somewhere they never went.
    - **More leaving than the pool holds.** Reachable whenever cash has gone
      negative from a missing transaction: 10k of cost base against -500 of cash
      is a pool basis of 9,500, and transferring every holding out would
      otherwise compute 1.05 and credit the receiver with deposits that never
      existed. Clamping moves the whole pool and no more.
    - **Negative pool basis.** Would flip the sign and make a `TFR_OUT` *add*
      deposits.

    Args:
        value_leaving: CAD value the leg takes out, non-negative.
        basis_cad: The pool's cost base plus cash, before the leg is applied.

    Returns:
        A fraction in `[0, 1]`.
    """
    if basis_cad <= ZERO or value_leaving <= ZERO:
        return ZERO
    if value_leaving >= basis_cad:
        return ONE
    return value_leaving / basis_cad


def _add(amounts: _PerCurrency, currency: Currency, amount: Decimal) -> None:
    """Add to one currency's running amount."""
    amounts[currency] = amounts.get(currency, ZERO) + amount


@dataclass
class _PoolFigures:
    """One tracked pool's running figures."""

    basis_cad: Decimal = ZERO
    deposits: _PerCurrency = field(default_factory=dict)
    contributions: _PerCurrency = field(default_factory=dict)
    withdrawals: _PerCurrency = field(default_factory=dict)


class NetDeposits:
    """Running net deposits for the pools that transfers actually cross.

    Deliberately **not** every pool. Without a transfer, net deposits is simply
    contributions minus withdrawals, and this is the replay's hot path. A pair inside
    one pool at a grain moves nothing there, which keeps the folio grain at plain
    contributions less withdrawals.
    """

    def __init__(
        self,
        rows: Sequence[LedgerRow],
        pair_of: Mapping[int, TransferPair],
        fx: FxRates,
    ) -> None:
        """Size the tracker from a finished cost-base walk.

        Args:
            rows: Every ledger row. Only the transfer legs are kept.
            pair_of: Each paired leg's transfer, by TxnId.
            fx: Rates for converting cash into the CAD pool basis is measured in.
        """
        self._fx = fx
        self._pair_of = pair_of
        self._legs = {
            computed.row.txn_id: computed
            for computed in rows
            if computed.row.action in TRANSFER_ACTIONS
        }
        self._tracked: dict[Pool, _PoolFigures] = {}
        for pair in pair_of.values():
            for crossing in pair.crossings:
                self._tracked.setdefault(crossing.out_pool, _PoolFigures())
                self._tracked.setdefault(crossing.in_pool, _PoolFigures())
        self._tracked_grains = tuple(
            dict.fromkeys(pool.grain for pool in self._tracked),
        )
        # Deposits taken by whichever leg was reached first, for the other to use.
        self._carried: dict[tuple[int, Grain], _PerCurrency] = {}
        self._totals: dict[CashKey, TransferTotals] = {}
        self.shares: list[DepositShare] = []

    def apply(self, computed: LedgerRow, effects: Sequence[CashEffect]) -> None:
        """Book one row, in settlement-date order.

        A transfer's share is measured against the pool as the leg found it, so the
        row's own cash and cost base reach the pool basis last.

        Args:
            computed: The row, with the cost base it moved at each grain.
            effects: What the row does to cash.
        """
        row = computed.row
        if row.action in TRANSFER_ACTIONS:
            self._apply_leg(computed)
        tracked = self._tracked_pools_of(computed)
        if not tracked:
            return
        if row.action is Action.CONTRIBUTION:
            self._apply_external(row, tracked, abs(row.amount or ZERO))
        elif row.action is Action.WITHDRAWAL:
            self._apply_external(row, tracked, -abs(row.amount or ZERO))
        self._advance_basis(computed, tracked, effects)

    def transfer_totals(self) -> dict[CashKey, TransferTotals]:
        """Return what transfers added up to, for every pool one touched.

        A tracked pool's `transfers` is restated as the exact complement of the
        deposits it holds, so `net deposits = contributions - withdrawals +
        transfers` balances. A pool whose transfers all went unpaired keeps its
        face-value total, since nothing measured its deposits.

        Returns:
            Each pool and currency's totals.
        """
        for pool, figures in self._tracked.items():
            for currency, deposited in figures.deposits.items():
                self._totals_of(pool, currency).transfers = (
                    deposited
                    - figures.contributions.get(currency, ZERO)
                    + figures.withdrawals.get(currency, ZERO)
                )
        return self._totals

    def _tracked_pools_of(
        self,
        computed: LedgerRow,
    ) -> list[tuple[Pool, _PoolFigures]]:
        """List the tracked pools a row sits in, one per grain at most."""
        tracked = []
        for grain in self._tracked_grains:
            pool = Pool.of(grain, computed.row, computed.acct_type)
            figures = self._tracked.get(pool)
            if figures is not None:
                tracked.append((pool, figures))
        return tracked

    def _totals_of(self, pool: Pool, currency: Currency) -> TransferTotals:
        """Return one pool and currency's transfer totals, creating them if new."""
        return self._totals.setdefault((pool, currency), TransferTotals())

    def _apply_external(
        self,
        row: Txn,
        tracked: list[tuple[Pool, _PoolFigures]],
        amount: Decimal,
    ) -> None:
        """Add a contribution (positive) or withdrawal (negative) to its pools."""
        for _pool, figures in tracked:
            _add(figures.deposits, row.currency, amount)
            if amount > ZERO:
                _add(figures.contributions, row.currency, amount)
            else:
                _add(figures.withdrawals, row.currency, -amount)

    def _advance_basis(
        self,
        computed: LedgerRow,
        tracked: list[tuple[Pool, _PoolFigures]],
        effects: Sequence[CashEffect],
    ) -> None:
        """Move the pool basis by the row's cash and the cost base it changed.

        Both land on the **settlement date**, even though the cost base was computed
        in transaction-date order. A buy on T settling T+2 raises the cost base on T
        and drops cash on T+2; booking both here leaves the pool basis untouched by
        a purchase, so only genuine growth moves it.
        """
        fx_date = computed.row.fx_date
        cash_in_cad = [
            (
                self._fx.to_cad(effect.cash, fx_date, effect.currency).value,
                effect.grains,
            )
            for effect in effects
            if effect.cash != ZERO
        ]
        for pool, figures in tracked:
            for amount, grains in cash_in_cad:
                if pool.grain in grains:
                    figures.basis_cad += amount
            delta = computed.measures(pool.grain).delta_cad
            if delta != ZERO:
                figures.basis_cad += delta

    def _apply_leg(self, computed: LedgerRow) -> None:
        """Move one transfer leg's deposits, paired or not."""
        pair = self._pair_of.get(computed.row.txn_id)
        if pair is None:
            self._apply_unpaired(computed)
        else:
            self._apply_paired(computed, pair)

    def _apply_paired(self, computed: LedgerRow, pair: TransferPair) -> None:
        """Move a paired leg's share of deposits at every grain the pair crosses."""
        row = computed.row
        arriving = row.action is Action.TFR_IN
        for crossing in pair.crossings:
            pool = crossing.in_pool if arriving else crossing.out_pool
            self._record_transferred_value(computed, pool)
            for currency, amount in self._share_of(pair, crossing).items():
                if arriving:
                    _add(self._tracked[pool].deposits, currency, amount)
                self._record_share(
                    row,
                    pool,
                    currency,
                    amount if arriving else -amount,
                )

    def _share_of(self, pair: TransferPair, crossing: Crossing) -> _PerCurrency:
        """Return the deposits a pair moves at one grain, taking them only once.

        Whichever leg is reached first settles the sending side. An in leg settling
        first is reported until it is re-dated, and must not lose its deposits
        meanwhile.
        """
        key = (pair.pair_id, crossing.grain)
        moved = self._carried.get(key)
        if moved is None:
            moved = self._take(pair, crossing)
            self._carried[key] = moved
        return moved

    def _take(self, pair: TransferPair, crossing: Crossing) -> _PerCurrency:
        """Remove the sending pool's share of its deposits for one pair."""
        figures = self._tracked[crossing.out_pool]
        share = fraction_leaving(
            self._leg_value_cad(pair.out_leg, crossing.grain),
            figures.basis_cad,
        )
        moved: _PerCurrency = {}
        if share == ZERO:
            return moved
        for currency, amount in figures.deposits.items():
            if amount == ZERO:
                continue
            portion = amount * share
            remaining = amount - portion
            if abs(remaining) < DEPOSIT_DUST:
                # The pool emptied. Hand the residue over rather than stranding a
                # fraction of a cent that no later leg can move and that reads as a
                # non-zero denominator.
                portion, remaining = amount, ZERO
            moved[currency] = portion
            figures.deposits[currency] = remaining
        return moved

    def _leg_value_cad(self, leg: Txn, grain: Grain) -> Decimal:
        """Return what one leg removes from its pool, in CAD and non-negative.

        A cash leg is worth its amount. An in-kind leg is worth the cost base it
        carried, read off the cost-base walk so the two can never disagree, and
        because that is what the pool basis is measured in.
        """
        if leg.is_in_kind_transfer:
            return abs(self._legs[leg.txn_id].measures(grain).delta_cad)
        return abs(self._fx.to_cad(leg.amount or ZERO, leg.fx_date, leg.currency).value)

    def _apply_unpaired(self, computed: LedgerRow) -> None:
        """Fall back to face value for a cash leg with no counterpart.

        With no pair there is no sending pool to measure a share against. An in-kind
        leg carries no cost base, so it moves no deposits at all.
        """
        row = computed.row
        if row.is_in_kind_transfer:
            return
        amount = row.amount or ZERO
        for grain in Grain:
            pool = Pool.of(grain, row, computed.acct_type)
            totals = self._totals_of(pool, row.currency)
            totals.transfers += amount
            totals.transfers_value += amount
            if amount == ZERO:
                continue
            figures = self._tracked.get(pool)
            if figures is not None:
                _add(figures.deposits, row.currency, amount)
            self._record_share(row, pool, row.currency, amount)

    def _record_transferred_value(self, computed: LedgerRow, pool: Pool) -> None:
        """Note what a leg actually moved, for reconciling against a statement."""
        row = computed.row
        if row.is_in_kind_transfer:
            moved, currency = computed.measures(pool.grain).delta_cad, Currency.CAD
        else:
            moved, currency = row.amount or ZERO, row.currency
        if moved != ZERO:
            self._totals_of(pool, currency).transfers_value += moved

    def _record_share(
        self,
        row: Txn,
        pool: Pool,
        currency: Currency,
        amount: Decimal,
    ) -> None:
        """Keep what one leg moved in one pool."""
        self.shares.append(
            DepositShare(row.txn_id, row.settle_date, pool, currency, amount),
        )
