"""An account's fee rules: where a commission sits in a trade, and how they are found.

Brokers disagree on two things. Whether a trade's `Amount` already contains the
commission (the **fee convention**), and which sign a charged commission is stored
with (the **fee sign**). Both are set per account in `config.yaml`, or left on `auto`
and worked out from the account's own trades. `FeeRules` holds the pair and answers
every question the replay has about a fee, so nothing else combines them.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

from domain import Action, FeeConvention, FeeSign
from domain.numeric import CENT, ONE, ZERO

if TYPE_CHECKING:
    from collections.abc import Sequence

    from models import Txn

logger = logging.getLogger(__name__)

# Baseline slack when reconciling a trade's Amount against Price * Units.
FEE_MATCH_TOLERANCE = CENT

# Brokers can round Price to 2dp, so Price * Units is out by up to half a cent
# per unit regardless of commission. Widen tolerance to capture subcent rounding for
# fee convention detection.
PRICE_ROUNDING_PER_UNIT = Decimal("0.005")

# Below this share of the vote, an account's fee convention is ambiguous.
FEE_MAJORITY_SHARE = 0.6

# What counts as a trade: the actions that exchange cash for units.
_TRADE_ACTIONS = (Action.BUY, Action.SELL)


@dataclass(frozen=True)
class TradeCash:
    """What a trade cost or realized, once the fee is placed correctly.

    Attributes:
        cost: What a BUY added to the cost base -> the amount plus commission.
        proceeds: What a SELL realized -> the amount less commission.
        commission: The commission as a cost: positive when charged, negative on
            the rare row that rebates one.
    """

    cost: Decimal
    proceeds: Decimal
    commission: Decimal


@dataclass(frozen=True)
class FeeRules:
    """How one account's fees work.

    A setting left on `AUTO` is unresolved: `resolve` finds it from the account's
    rows. Every question below needs both settled.

    Attributes:
        convention: Whether a trade's `Amount` already contains the commission.
        sign: Which sign a charged commission is stored with.
    """

    convention: FeeConvention = FeeConvention.AUTO
    sign: FeeSign = FeeSign.AUTO

    def resolve(self, account: str, rows: Sequence[Txn]) -> FeeRules:
        """Settle every `AUTO` setting from the account's own rows.

        The sign is the one most fee-bearing rows carry, since a broker that writes
        charges negative writes the occasional rebate positive. The convention is
        the majority over trades that reconcile with only one of the two. A
        setting that is already pinned is left alone.

        Args:
            account: The account name, for the warning on a near-even vote.
            rows: Every transaction of that account.

        Returns:
            These rules with nothing left on `AUTO`. An account with no fee to
            read from falls back to EXCLUDED and POSITIVE, which is what a
            hand-entered `--fee 4.95` means and is moot when every fee is zero.
        """
        sign = self.sign
        if sign is FeeSign.AUTO:
            sign = _majority_sign(rows)
        convention = self.convention
        if convention is FeeConvention.AUTO:
            convention = _majority_convention(
                account,
                rows,
                FeeRules(FeeConvention.AUTO, sign),
            )
        if (convention, sign) == (self.convention, self.sign):
            return self
        return replace(self, convention=convention, sign=sign)

    def commission(self, row: Txn) -> Decimal:
        """Return a row's fee as a cost: positive if charged, negative if rebated."""
        return (row.fee or ZERO) * self._multiplier()

    def trade_cash(self, row: Txn) -> TradeCash:
        """Resolve a trade's cost, proceeds and commission.

        Args:
            row: A BUY or SELL.

        Returns:
            The cost, proceeds and commission for this row.
        """
        self._require_convention()
        commission = self.commission(row)
        amount = abs(row.amount or ZERO)
        if self.convention is FeeConvention.INCLUDED:
            return TradeCash(cost=amount, proceeds=amount, commission=commission)
        return TradeCash(
            cost=amount + commission,
            proceeds=amount - commission,
            commission=commission,
        )

    def reconciles(self, row: Txn) -> bool:
        """Whether a trade's amount adds up under either fee convention.

        Args:
            row: A BUY or SELL.

        Returns:
            False when `Amount` matches neither `Price * Units` nor
            `Price * Units` with the fee applied, which means the row is wrong.
        """
        excluded_fits, included_fits = self._fits(row)
        return excluded_fits or included_fits

    def other_convention(self, row: Txn) -> FeeConvention | None:
        """Return the convention a trade fits when that is not this account's.

        Args:
            row: A BUY or SELL.

        Returns:
            The other convention when `Amount` fits only it, so the row goes against
            this account's. None when the row fits this one, fits both, or fits
            neither (`reconciles` reports that).
        """
        self._require_convention()
        pointed = self.points_at(row)
        return None if pointed is self.convention else pointed

    def _require_convention(self) -> None:
        """Refuse to answer before the convention is settled."""
        if self.convention is FeeConvention.AUTO:
            msg = "fee convention is unresolved; call resolve() first"
            raise ValueError(msg)

    def _multiplier(self) -> Decimal:
        """Return what turns a stored fee into a cost: -1 if negative, else +1."""
        if self.sign is FeeSign.AUTO:
            msg = "fee sign is unresolved; call resolve() first"
            raise ValueError(msg)
        return -ONE if self.sign is FeeSign.NEGATIVE else ONE

    def _fits(self, row: Txn) -> tuple[bool, bool]:
        """Reconcile one trade against both conventions.

        Returns:
            Whether it fits EXCLUDED (`Amount` is gross of the fee) and whether it
            fits INCLUDED (`Amount` already contains it). Both are true whenever
            the fee is zero, since the two conventions then coincide.
        """
        commission = self.commission(row)
        amount = abs(row.amount or ZERO)
        units = abs(row.units or ZERO)
        gross = abs(row.price or ZERO) * units
        tolerance = FEE_MATCH_TOLERANCE + PRICE_ROUNDING_PER_UNIT * units
        buying = row.action is Action.BUY
        included = gross + commission if buying else gross - commission
        return (
            abs(amount - gross) <= tolerance,
            abs(amount - included) <= tolerance,
        )

    def points_at(self, row: Txn) -> FeeConvention | None:
        """Which convention a single trade points at, or None when it cannot say."""
        excluded_fits, included_fits = self._fits(row)
        if excluded_fits and not included_fits:
            return FeeConvention.EXCLUDED
        if included_fits and not excluded_fits:
            return FeeConvention.INCLUDED
        return None


def _majority_sign(rows: Sequence[Txn]) -> FeeSign:
    """Pick the sign most fee-bearing rows carry; POSITIVE when none carries a fee."""
    votes = sum(
        1 if (row.fee or ZERO) > ZERO else -1
        for row in rows
        if (row.fee or ZERO) != ZERO
    )
    return FeeSign.NEGATIVE if votes < 0 else FeeSign.POSITIVE


def _majority_convention(
    account: str,
    rows: Sequence[Txn],
    signed: FeeRules,
) -> FeeConvention:
    """Pick an account's convention from its trades, warning on a near-even split.

    Every BUY or SELL carrying a non-zero fee is reconciled against `Price * Units`
    and `Price * Units` plus-or-minus the fee. Rows matching both carry no
    information, and rows matching neither abstain: they are reported on the row.
    """
    votes: Counter[FeeConvention] = Counter()
    for row in rows:
        if row.action in _TRADE_ACTIONS and row.fee:
            vote = signed.points_at(row)
            if vote is not None:
                votes[vote] += 1
    if not votes:
        return FeeConvention.EXCLUDED
    ranked = votes.most_common()
    winner, top = ranked[0]
    if top / sum(votes.values()) < FEE_MAJORITY_SHARE:
        logger.warning(
            "Fee convention for '%s' splits near evenly (%s); using %s. Set "
            "accounts.map for that account to settle it explicitly.",
            account,
            dict(ranked),
            winner,
        )
    return winner
