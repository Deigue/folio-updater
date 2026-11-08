"""Infer an account's type and margin from its name, and carry its configured fee rules.

Convention `<BROKER>-<TYPE>`
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import cache

from app import get_config
from domain import (
    ACCOUNT_TYPE_ALIASES,
    MARGIN_MARKER,
    AccountType,
)
from engine.fees import FeeRules

logger = logging.getLogger(__name__)

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

    Resolution order: an exact match in `accounts.map` (then its upper-cased
    name), then a right-to-left scan of the name's tokens (so `IBKR-TFSA`
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
    override = mapped.get(name) or mapped.get(name.upper())
    if override is not None:
        return Account(name, *override, fees=fees)

    if not naming:
        logger.debug("Naming convention disabled; %s is UNKNOWN", name)
        return Account(name, AccountType.UNKNOWN, fees=fees)

    upper = name.upper()
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
