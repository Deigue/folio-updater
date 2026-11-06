"""What each action means: its impact, the fields it needs and their signs.

One profile per action, so the replay, ingest and `folio add` read the same
facts instead of keeping their own copies.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from domain.enums import Action, Column, Impact, Sign

if TYPE_CHECKING:
    from collections.abc import Mapping

_AMOUNT = Column.Txn.AMOUNT
_PRICE = Column.Txn.PRICE
_UNITS = Column.Txn.UNITS
_TICKER = Column.Txn.TICKER

# Fields every transaction needs, whatever its action. Ingest rejects a row
# missing its date or currency, and the Txns table refuses one with no account.
ALWAYS_REQUIRED: tuple[Column.Txn, ...] = (
    Column.Txn.TXN_DATE,
    Column.Txn.CURRENCY,
    Column.Txn.ACCOUNT,
)


@dataclass(frozen=True)
class ActionProfile:
    """The facts one action fixes about its transactions.

    Attributes:
        impact: What the action does to the cost base.
        required: Fields the action needs on top of `ALWAYS_REQUIRED`.
        signs: The sign each constrained column must carry. A column left out
            may carry either sign.
    """

    impact: Impact
    required: frozenset[Column.Txn]
    signs: Mapping[Column.Txn, Sign]


def _make_profile(
    impact: Impact,
    required: set[Column.Txn],
    signs: dict[Column.Txn, Sign] | None = None,
) -> ActionProfile:
    """Build a profile with read-only collections."""
    return ActionProfile(impact, frozenset(required), MappingProxyType(signs or {}))


ACTION_PROFILES: Mapping[Action, ActionProfile] = MappingProxyType(
    {
        Action.BUY: _make_profile(
            Impact.ACB,
            {_AMOUNT, _PRICE, _UNITS, _TICKER},
            {_AMOUNT: Sign.NEGATIVE, _UNITS: Sign.POSITIVE},  # cash out, units in
        ),
        Action.SELL: _make_profile(
            Impact.ACB,
            {_AMOUNT, _PRICE, _UNITS, _TICKER},
            {_AMOUNT: Sign.POSITIVE, _UNITS: Sign.NEGATIVE},  # cash in, units out
        ),
        Action.ROC: _make_profile(
            Impact.ACB,
            {_AMOUNT, _TICKER},
            {_AMOUNT: Sign.POSITIVE},
        ),
        # Price holds the units before the split, Units the units after.
        Action.SPLIT: _make_profile(
            Impact.ACB,
            {_PRICE, _UNITS, _TICKER},
            {_PRICE: Sign.POSITIVE, _UNITS: Sign.POSITIVE},
        ),
        # A transfer carries either cash or units, so neither is required on its
        # own: a cash transfer has Amount and no Ticker, an in-kind transfer has
        # Ticker and Units and no Amount.
        Action.TFR_IN: _make_profile(
            Impact.ACB,
            set(),
            {_AMOUNT: Sign.POSITIVE, _UNITS: Sign.POSITIVE},
        ),
        Action.TFR_OUT: _make_profile(
            Impact.ACB,
            set(),
            {_AMOUNT: Sign.NEGATIVE, _UNITS: Sign.NEGATIVE},
        ),
        Action.DIVIDEND: _make_profile(Impact.INCOME, {_AMOUNT, _TICKER}),
        # Either sign: a charge or income.
        Action.FCH: _make_profile(Impact.INCOME, {_AMOUNT}),
        Action.CONTRIBUTION: _make_profile(
            Impact.NONE,
            {_AMOUNT},
            {_AMOUNT: Sign.POSITIVE},
        ),
        Action.WITHDRAWAL: _make_profile(
            Impact.NONE,
            {_AMOUNT},
            {_AMOUNT: Sign.NEGATIVE},
        ),
        # Either sign: the side of the conversion this row records.
        Action.FXT: _make_profile(Impact.NONE, {_AMOUNT}),
    },
)
