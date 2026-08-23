"""Track how much of a pool's own money a transfer carries with it.

`Net Deposited` tracks "how much of the holder's own money is in here", so a
transfer between two accounts the holder already owns must move the **deposit**
portion of what it carries, and nothing else. This is more complex to calculate
accurately than how brokers calculate:

- **Face value** moves too much. Sell a position at a profit and sweep the cash
  to another broker, then taking face value means you are counting gains as part of the
  pools "deposit"
- **Cost base** moves the wrong thing. A dividend reinvested at the old broker
  raises the cost base without any new money arriving, so ACB books that growth
  as a deposit too.

The rule instead is proportional. A transfer that removes `value_leaving` from a pool
having `book_cad` cost base and cash, then the **real** moved is:

    f       = value_leaving / book_cad
    moved   = f * deposits

`book_cad` is **cost base plus cash**, not market value.
For a pool holding X (cost 5k, worth 5k) and Y (cost 5k, worth 10k) with 10k of deposits
moving only Y takes 5k of deposits on book basis and 6.67k on market basis: book basis
leaves Y's gain attached to Y at its new account, where it was earned, instead of
smearing it across both.

Two properties make this safe to rely on:

- **Both legs move the same vector.** The out leg computes it, the in leg
  applies it unchanged, so nothing is created or destroyed in transit.
- **`deposits / book_cad` is invariant under a transfer.** Ten in-kind legs on
  one date therefore produce the same result in any order, which is what lets a
  full account transfer land exactly on the contributions that funded it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from domain.numeric import ONE, ZERO

if TYPE_CHECKING:
    from collections.abc import Iterable

    from domain import Currency, Scope

# A pool at one grain. The scope is part of the key because an account may
# legitimately be named after a type, exactly as `CashKey` guards against.
PoolId = tuple["Scope", str]

# Deposits per currency. Never blended: a deposit is denominated in the currency
# it was made in, whatever asset it later became.
Deposits = dict["Currency", "Decimal"]

# A pool emptied by several legs divides by a slightly smaller book value each time,
# and fractions do not recombine exactly, so a fully transferred account can be left
# holding a millionth of a cent of deposits.
DEPOSIT_DUST = Decimal("0.005")


def fraction_leaving(value_leaving: Decimal, book_cad: Decimal) -> Decimal:
    """Return what share of a pool a leg removes, clamped to a usable range.

    Args:
        value_leaving: CAD value the leg takes out, non-negative.
        book_cad: The pool's cost base plus cash, before the leg is applied.

    Returns:
        A fraction in `[0, 1]`.

    Guards against these scenario:

    - **Empty pool.** Dividing by zero book value is undefined, and nothing of
      value is leaving, so nothing is taken. Deposits may still be non-zero
      here (contribute, then lose it all to fees), in which case they stay
      stranded in the empty pool rather than moving somewhere they never went.
    - **More leaving than the pool holds.** Reachable whenever cash has gone
      negative from a missing transaction: 10k of cost base against -500 of cash
      is a book value of 9,500, and transferring every position out would
      otherwise compute 1.05 and credit the receiver with deposits that never
      existed. Clamping moves the whole pool and no more.
    - **Negative book value.** Would flip the sign and make a `TFR_OUT` *add*
      deposits.
    """
    if book_cad <= ZERO or value_leaving <= ZERO:
        return ZERO
    if value_leaving >= book_cad:
        return ONE
    return value_leaving / book_cad


@dataclass
class PoolDeposits:
    """One pool's running book value and deposits.

    Attributes:
        book_cad: Cost base plus cash, in CAD. The denominator of the
            proportional rule, and the only reason book value is tracked at all.
        deposits: Net deposits so far, per currency, signed.
    """

    book_cad: Decimal = ZERO
    deposits: Deposits = field(default_factory=dict)


class DepositLedger:
    """Running deposits for the pools that transfers actually touch.

    Deliberately **not** every pool. When transfers not involved, net deposits is simply
    contributions minus withdrawals. This ledger tracking is only needed when transfers
    are involved.
    """

    def __init__(self, tracked: frozenset[PoolId]) -> None:
        """Create a ledger for a fixed set of pools.

        Args:
            tracked: The pools that participate in a transfer at their grain.
        """
        # scope -> [pool name, PoolDeposits]
        self._by_scope: dict[Scope, dict[str, PoolDeposits]] = {}
        for scope, name in tracked:
            self._by_scope.setdefault(scope, {})[name] = PoolDeposits()
        # Deposits taken out by transfers, waiting for a transfer in.
        self._carried: dict[tuple[int, Scope], Deposits] = {}
        self.scopes: tuple[Scope, ...] = tuple(self._by_scope)

    @property
    def active(self) -> bool:
        """Whether any pool needs tracking at all."""
        return bool(self._by_scope)

    def resolve(self, scope: Scope, name: str) -> PoolDeposits | None:
        """Return a pool's running state, or None when it is not tracked."""
        pools = self._by_scope.get(scope)
        return None if pools is None else pools.get(name)

    def pools(self) -> Iterable[tuple[PoolId, PoolDeposits]]:
        """Iterate every tracked pool alongside the grain it sits at."""
        return (
            ((scope, name), state)
            for scope, pools in self._by_scope.items()
            for name, state in pools.items()
        )

    def deposit(
        self,
        scope: Scope,
        name: str,
        currency: Currency,
        amount: Decimal,
    ) -> None:
        """Add a contribution (positive) or withdrawal (negative) to a pool."""
        state = self.resolve(scope, name)
        if state is None or amount == ZERO:
            return
        state.deposits[currency] = state.deposits.get(currency, ZERO) + amount

    def take(
        self,
        scope: Scope,
        name: str,
        pair_id: int,
        value_leaving_cad: Decimal,
    ) -> Deposits:
        """Remove a leg's share of a pool's deposits, holding it for the in leg.

        Must be called **before** the leg's own effect on cash or cost base is
        applied, so the fraction is measured against the pool as the leg found
        it.

        Args:
            scope: The grain being applied, part of the carry key.
            name: The pool the value is leaving.
            pair_id: Identifies the transfer, so the in leg can claim this.
            value_leaving_cad: CAD value the leg removes, non-negative.

        Returns:
            The deposits taken, per currency.
        """
        moved: Deposits = {}
        state = self.resolve(scope, name)
        if state is not None:
            share = fraction_leaving(value_leaving_cad, state.book_cad)
            if share != ZERO:
                for currency, amount in state.deposits.items():
                    if amount == ZERO:
                        continue
                    portion = amount * share
                    remaining = amount - portion
                    if abs(remaining) < DEPOSIT_DUST:
                        # The pool emptied. Hand the residue over rather than
                        # stranding a fraction of a cent that no later leg can
                        # move and that reads as a non-zero denominator.
                        portion, remaining = amount, ZERO
                    moved[currency] = portion
                    state.deposits[currency] = remaining
        self._carried[(pair_id, scope)] = moved
        return moved

    def give(self, scope: Scope, name: str, pair_id: int) -> Deposits:
        """Add the deposits the out leg handed over to the receiving pool.

        The out leg must already have been applied: the caller checks `pending`
        and settles the sending side first when the legs arrive out of order, so
        a missing entry is a bug in that sequencing rather than a state worth
        tolerating, and is left to raise.

        Returns:
            The deposits added, per currency.
        """
        moved = self._carried[(pair_id, scope)]
        state = self.resolve(scope, name)
        if state is not None:
            for currency, amount in moved.items():
                state.deposits[currency] = state.deposits.get(currency, ZERO) + amount
        return moved

    def pending(self, pair_id: int, scope: Scope) -> bool:
        """Whether this pair's out leg has been applied yet at this grain."""
        return (pair_id, scope) in self._carried
