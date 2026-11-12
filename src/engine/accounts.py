"""Accounts, the pools they form, and what an account's name says about it.

Convention `<BROKER>-<TYPE>`
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import cache
from typing import TYPE_CHECKING, NamedTuple

from app import get_config
from domain import (
    ACCOUNT_TYPE_ALIASES,
    MARGIN_MARKER,
    TAXABLE_ACCOUNT_TYPES,
    AccountType,
    Grain,
)
from engine.fees import FeeRules

if TYPE_CHECKING:
    from models import Txn

logger = logging.getLogger(__name__)

# What reports call the folio pool.
FOLIO_LABEL = "Portfolio"


class Pool(NamedTuple):
    """Which pool: an account, an account type, or the whole folio.

    Attributes:
        grain: The grain the pool sits at.
        name: The account name or account type its rows are filtered to. Empty
            at folio grain, where nothing is filtered out.
    """

    grain: Grain
    name: str

    @classmethod
    def of(cls, grain: Grain, row: Txn, acct_type: AccountType) -> Pool:
        """Return the pool a transaction belongs to at one grain."""
        if grain is Grain.ACCOUNT:
            return cls(grain, row.account)
        if grain is Grain.TYPE:
            return cls(grain, str(acct_type))
        return FOLIO

    @property
    def label(self) -> str:
        """How the pool reads in a heading.

        Returns:
            The account name, the account type with `-` for `_`
            (`NON-REGISTERED`), or `Portfolio` for the whole folio.
        """
        if self.grain is Grain.TYPE:
            return self.name.replace("_", "-")
        if self.grain is Grain.ACCOUNT:
            return self.name
        return FOLIO_LABEL

    @property
    def taxed(self) -> bool:
        """Whether the CRA taxes the pool's sales: a non-registered or corporate pool.

        The folio pool blends registered with taxable accounts, so it is never
        taxed as a whole.
        """
        return account_type_of(self) in TAXABLE_ACCOUNT_TYPES


FOLIO = Pool(Grain.FOLIO, "")

# `<BROKER>-<TYPE>` is the convention, but `_` and whitespace show up too.
_TOKEN_SPLIT = re.compile(r"[-_\s]+")


@dataclass(frozen=True)
class Account:
    """One account at one broker, as read from its name or `accounts.map`.

    Attributes:
        name: The account name as stored on its transactions.
        account_type: Its tax treatment.
        margin: Whether it is a margin account: non-registered, allowed to borrow.
        fees: Its fee rules as configured. Any setting left on `auto` stays
            unresolved here, since only the account's rows can settle it: the
            replay resolves them before it reads a fee.
    """

    name: str
    account_type: AccountType
    margin: bool = False
    fees: FeeRules = field(default_factory=FeeRules)


# Every word an account name may carry that says what the account is: each
# type's own name, its aliases, and `MARGIN`, which means non-registered with
# margin. Each maps to the type and margin it means.
_MARKERS: dict[str, tuple[AccountType, bool]] = {
    **{
        str(member): (member, False)
        for member in AccountType
        if member is not AccountType.UNKNOWN
    },
    **{alias: (member, False) for alias, member in ACCOUNT_TYPE_ALIASES.items()},
    MARGIN_MARKER: (AccountType.NON_REGISTERED, True),
}

# Every `accounts.map` input to resolution, as a hashable cache key: each mapped
# account's name, type and margin.
_Overrides = tuple[tuple[str, AccountType, bool], ...]


def resolve_account(name: str) -> Account:
    """Read what an account is from its name, or from its `accounts.map` entry.

    Resolution order: a match in `accounts.map` (whose keys are upper-case),
    then a right-to-left scan of the name's tokens (so `IBKR-TFSA`
    resolves on `TFSA`, not on a broker code that happens to collide), then a
    substring scan of the whole name (which catches `WSTFSA`), then UNKNOWN. A
    `MARGIN` token or map value means non-registered with margin: margin is not
    a tax treatment.

    Args:
        name: The account name as stored on the transaction.

    Returns:
        The account, typed `AccountType.UNKNOWN` when nothing resolves it.
    """
    config = get_config()
    fees = FeeRules(config.fee_convention_for(name), config.fee_sign_for(name))
    return _resolve_against(
        name,
        _override_key(),
        fees,
        naming=config.account_naming_convention,
    )


def _override_key() -> _Overrides:
    """Snapshot every `accounts.map` type and margin as a hashable cache key."""
    return tuple(
        (name, entry["type"], bool(entry.get("margin")))
        for name, entry in sorted(get_config().account_map.items())
        if entry.get("type")
    )


@cache
def _resolve_against(
    name: str,
    overrides: _Overrides,
    fees: FeeRules,
    *,
    naming: bool,
) -> Account:
    """Resolve an account against fixed config inputs, once per input."""
    mapped = {key: (account_type, margin) for key, account_type, margin in overrides}
    upper = name.upper()
    override = mapped.get(upper)
    if override is not None:
        return Account(name, *override, fees=fees)

    if not naming:
        logger.debug("Naming convention disabled; %s is UNKNOWN", name)
        return Account(name, AccountType.UNKNOWN, fees=fees)

    tokens = [token for token in _TOKEN_SPLIT.split(upper) if token]
    for token in reversed(tokens):
        if token in _MARKERS:
            return Account(name, *_MARKERS[token], fees=fees)

    # Longest first, so `NONREGISTERED` is not shadowed by `NONREG`.
    for marker in sorted(_MARKERS, key=len, reverse=True):
        if marker in upper:
            return Account(name, *_MARKERS[marker], fees=fees)

    # The cache dedupes this, so an unknown account is reported once.
    logger.warning("Could not infer an account type for '%s'", name)
    return Account(name, AccountType.UNKNOWN, fees=fees)


def account_type_of(pool: Pool) -> AccountType | None:
    """Name the account type a pool is, when it is exactly one.

    Contribution room only means something for a single account type, so the
    folio pool has none.

    Args:
        pool: The pool being reported.

    Returns:
        The account type, or None when the pool spans more than one.
    """
    if pool.grain is Grain.TYPE:
        return AccountType(pool.name)
    if pool.grain is Grain.ACCOUNT:
        return resolve_account(pool.name).account_type
    return None
