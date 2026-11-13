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

# Fixed spellings read as an action wherever an action is typed or imported.
# The spaced forms only match text that is one field, such as a source column.
ACTION_ALIASES: Mapping[str, Action] = MappingProxyType(
    {
        "B": Action.BUY,
        "BUYS": Action.BUY,
        "PURCHASE": Action.BUY,
        "BOUGHT": Action.BUY,
        "S": Action.SELL,
        "SELLS": Action.SELL,
        "SOLD": Action.SELL,
        "SALE": Action.SELL,
        "DIV": Action.DIVIDEND,
        "DIVS": Action.DIVIDEND,
        "DIVIDENDS": Action.DIVIDEND,
        "TRANSFER_IN": Action.TFR_IN,
        "TRANSFER IN": Action.TFR_IN,
        "TFRIN": Action.TFR_IN,
        "TRANSFER_OUT": Action.TFR_OUT,
        "TRANSFER OUT": Action.TFR_OUT,
        "TFROUT": Action.TFR_OUT,
        "CONTRIB": Action.CONTRIBUTION,
        "CONTRIBUTIONS": Action.CONTRIBUTION,
        "DEP": Action.CONTRIBUTION,
        "DEPOSIT": Action.CONTRIBUTION,
        "DEPOSITS": Action.CONTRIBUTION,
        "FEE": Action.FCH,
        "FEES": Action.FCH,
        "INTEREST": Action.FCH,
        "FOREX": Action.FXT,
        "FX": Action.FXT,
        "CURRENCY": Action.FXT,
        "RETURN_OF_CAPITAL": Action.ROC,
        "STOCK_SPLIT": Action.SPLIT,
        "WITHDRAW": Action.WITHDRAWAL,
        "WITHDRAWALS": Action.WITHDRAWAL,
        "CASH_OUT": Action.WITHDRAWAL,
    },
)


def resolve_action(text: str) -> Action | None:
    """Read an action or one of its aliases, ignoring case and surrounding space."""
    key = text.strip().upper()
    if (alias := ACTION_ALIASES.get(key)) is not None:
        return alias
    try:
        return Action(key)
    except ValueError:
        return None
