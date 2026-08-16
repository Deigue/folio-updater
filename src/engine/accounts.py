"""Infer an account's tax type and fee convention from its name.

Convention `<BROKER>-<TYPE>`
"""

from __future__ import annotations

import logging
import re
from functools import cache

from app import get_config
from utils.constants import (
    ACCOUNT_TYPE_ALIASES,
    TAXABLE_ACCOUNT_TYPES,
    AccountType,
    FeeConvention,
)

logger = logging.getLogger(__name__)

# `<BROKER>-<TYPE>` is the convention, but `_` and whitespace show up too.
_TOKEN_SPLIT = re.compile(r"[-_\s]+")


def _override_key() -> tuple[tuple[str, str], ...]:
    """Snapshot every config input to resolution as a hashable cache key."""
    configured = get_config().account_types
    return tuple(sorted((name, str(value)) for name, value in configured.items()))


def resolve_account_type(account: str) -> AccountType:
    """Determine the tax type of an account from its name.

    Resolution order: an exact match in `accounts.map`, then a right-to-left
    scan of the name's tokens (so `IBKR-TFSA` resolves on `TFSA`, not on a
    broker code that happens to collide), then a substring scan of the whole
    name (which catches `WSTFSA`), then UNKNOWN.

    Args:
        account: The account name as stored on the transaction.

    Returns:
        The resolved `AccountType`, or `AccountType.UNKNOWN`.
    """
    config = get_config()
    return _resolve_account_type(
        account,
        _override_key(),
        naming=config.account_naming_convention,
    )


@cache
def _resolve_account_type(
    account: str,
    overrides: tuple[tuple[str, str], ...],
    *,
    naming: bool,
) -> AccountType:
    """Resolve a type against fixed config inputs."""
    override = dict(overrides).get(account) or dict(overrides).get(account.upper())
    if override is not None:
        return AccountType(override)

    if not naming:
        logger.debug("Naming convention disabled; %s is UNKNOWN", account)
        return AccountType.UNKNOWN

    name = account.upper()
    tokens = [token for token in _TOKEN_SPLIT.split(name) if token]
    for token in reversed(tokens):
        matched = _match_token(token)
        if matched is not None:
            return matched

    for candidate, matched in _substring_candidates():
        if candidate in name:
            return matched

    # The cache dedupes this, so an unknown account is reported once.
    logger.warning("Could not infer an account type for '%s'", account)
    return AccountType.UNKNOWN


def _match_token(token: str) -> AccountType | None:
    """Match one name token against the type names and their aliases."""
    if token in ACCOUNT_TYPE_ALIASES:
        return ACCOUNT_TYPE_ALIASES[token]
    try:
        return AccountType(token)
    except ValueError:
        return None


def _substring_candidates() -> list[tuple[str, AccountType]]:
    """Type names and aliases to scan for inside an unpunctuated name.

    Longest first, so `NONREGISTERED` is not shadowed by `NONREG`.
    """
    candidates: list[tuple[str, AccountType]] = [
        (str(member), member)
        for member in AccountType
        if member is not AccountType.UNKNOWN
    ]
    candidates.extend(ACCOUNT_TYPE_ALIASES.items())
    return sorted(candidates, key=lambda pair: len(pair[0]), reverse=True)


def fee_convention_for(account: str) -> FeeConvention:
    """Return the configured fee convention for an account.

    Args:
        account: The account name as stored on the transaction.

    Returns:
        The account's explicit override, else the configured default, which
        ships as `AUTO` so the convention is detected from the rows themselves.
    """
    return get_config().fee_convention_for(account)


def is_taxable(account: str) -> bool:
    """Whether dispositions in this account are taxable.

    Args:
        account: The account name as stored on the transaction.

    Returns:
        True for non-registered, margin and corporate accounts.
    """
    return resolve_account_type(account) in TAXABLE_ACCOUNT_TYPES
