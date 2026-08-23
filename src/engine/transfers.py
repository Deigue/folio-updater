"""Pair `TFR_OUT` legs with the `TFR_IN` legs that receive them.

A transfer is never a disposition: units and cost base move between pools and
the realized gain on the pair is always zero. Pairing them up front is what lets
the replay carry the cost base across, which it must

Two shapes exist:

- **Same account, same settle date**: a currency journal.
- **Cross account, same symbol, same settle date**: an in-kind move between
  two accounts you own, equal magnitude and opposing sign.

Cash txn pair the same way but move no units and no cost base; they matter only
to the cash replay.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from itertools import count
from typing import TYPE_CHECKING

from domain import Action

if TYPE_CHECKING:
    from collections.abc import Sequence

    from engine.types import TxnRow

DUST_UNITS = Decimal("1e-9")
CASH_TOLERANCE = Decimal("0.01")

# Institutional transfer window for settlement.
TRANSFER_WINDOW_DAYS = 5

# Tolerance for fractional leftovers to snap.
UNIT_MATCH_TOLERANCE = Decimal("0.0001")


def _shift(settle: str, days: int) -> str:
    """Move an ISO date by whole days, leaving anything unparseable alone."""
    try:
        return (date.fromisoformat(settle) + timedelta(days=days)).isoformat()
    except ValueError:
        return settle


def _day_gap(left: str, right: str) -> int:
    """Whole days between two ISO dates, zero when either is unparseable."""
    try:
        return abs((date.fromisoformat(left) - date.fromisoformat(right)).days)
    except ValueError:
        return 0


@dataclass(frozen=True)
class TransferPair:
    """A matched `TFR_OUT` / `TFR_IN` pair.

    Attributes:
        pair_id: Identifier shared by the two legs, so the replay can hand the
            cost base removed from the out pool to the in pool.
        out_leg: The leg the units or cash left.
        in_leg: The leg they arrived on.
        moves_units: True for a position transfer, False for a cash one.
    """

    pair_id: int
    out_leg: TxnRow
    in_leg: TxnRow
    moves_units: bool


# Preference order for candidate matches, lowest first. A cross-account move of
# the same symbol is the least ambiguous reading, a same-account pair can only
# be a currency journal, which by definition changes the symbol.
_SAME_SYMBOL_CROSS_ACCOUNT = 0
_JOURNAL_SAME_ACCOUNT = 1
_CROSS_ACCOUNT_ANY_SYMBOL = 2


def _journal_fits(out_leg: TxnRow, in_leg: TxnRow) -> bool:
    """Whether a same-account pair can be a currency journal.

    Held to an exact settle date, unlike a cross-account transfer.
    """
    return out_leg.settle_date == in_leg.settle_date


def _position_score(out_leg: TxnRow, in_leg: TxnRow) -> int | None:
    """Rank a candidate in leg for a position transfer, or None if impossible."""
    if not in_leg.is_position_transfer:
        return None
    if abs(abs(out_leg.units) - abs(in_leg.units)) > UNIT_MATCH_TOLERANCE:
        return None
    same_account = out_leg.account == in_leg.account
    same_symbol = out_leg.ticker == in_leg.ticker
    if same_symbol and not same_account:
        return _SAME_SYMBOL_CROSS_ACCOUNT
    if same_account and not same_symbol:
        return _JOURNAL_SAME_ACCOUNT if _journal_fits(out_leg, in_leg) else None
    if not same_account:
        return _CROSS_ACCOUNT_ANY_SYMBOL
    return None


def _cash_score(out_leg: TxnRow, in_leg: TxnRow) -> int | None:
    """Rank a candidate in leg for a cash transfer, or None if impossible."""
    if in_leg.is_position_transfer:
        return None
    if abs(abs(out_leg.amount) - abs(in_leg.amount)) > CASH_TOLERANCE:
        return None
    if out_leg.account != in_leg.account:
        return _SAME_SYMBOL_CROSS_ACCOUNT
    # Same account, so this is the cash side of a currency journal.
    return _JOURNAL_SAME_ACCOUNT if _journal_fits(out_leg, in_leg) else None


def pair_transfers(
    rows: Sequence[TxnRow],
) -> tuple[list[TransferPair], list[TxnRow]]:
    """Match every transfer leg with its counterpart.

    Args:
        rows: All transactions, in any order.

    Returns:
        The matched pairs, and the legs left with no counterpart.
    """
    out_legs = [row for row in rows if row.action is Action.TFR_OUT]
    in_legs = [row for row in rows if row.action is Action.TFR_IN]
    out_legs.sort(key=lambda row: (row.settle_date, row.txn_id))
    in_legs.sort(key=lambda row: (row.settle_date, row.txn_id))
    in_dates = [leg.settle_date for leg in in_legs]

    consumed: set[int] = set()
    pairs: list[TransferPair] = []
    ids = count(1)

    for out_leg in out_legs:
        # Every tfr_in settling within the window, found by bisection on the dates
        settle = out_leg.settle_date
        start = bisect_left(in_dates, _shift(settle, -TRANSFER_WINDOW_DAYS))
        end = bisect_right(in_dates, _shift(settle, TRANSFER_WINDOW_DAYS))
        candidates = in_legs[start:end]
        score_fn = _position_score if out_leg.is_position_transfer else _cash_score
        best: tuple[int, int, int, TxnRow] | None = None
        for candidate in candidates:
            if candidate.txn_id in consumed:
                continue
            score = score_fn(out_leg, candidate)
            if score is None:
                continue
            # Interpretation first, then the closest date, then the oldest row:
            # a same-date match still wins among equally plausible readings.
            gap = _day_gap(out_leg.settle_date, candidate.settle_date)
            key = (score, gap, candidate.txn_id, candidate)
            if best is None or key[:3] < best[:3]:
                best = key
        if best is None:
            continue
        consumed.add(best[3].txn_id)
        pairs.append(
            TransferPair(
                pair_id=next(ids),
                out_leg=out_leg,
                in_leg=best[3],
                moves_units=out_leg.is_position_transfer,
            ),
        )

    matched = {pair.out_leg.txn_id for pair in pairs} | consumed
    unpaired = [
        row
        for row in rows
        if row.action in (Action.TFR_IN, Action.TFR_OUT) and row.txn_id not in matched
    ]
    unpaired.sort(key=lambda row: (row.settle_date, row.txn_id))
    return pairs, unpaired
