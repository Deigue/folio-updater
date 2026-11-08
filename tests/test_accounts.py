"""Tests for inferring an account's type, margin and fee convention from its name."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from domain import AccountType, FeeConvention, FeeSign
from engine.accounts import Account, resolve_account
from engine.fees import FeeRules

if TYPE_CHECKING:
    from .helpers.context import TempContext

ACCOUNT_NAMES = [
    ("IBKR-PERSONAL", AccountType.NON_REGISTERED),
    ("WS-PERSONAL", AccountType.NON_REGISTERED),
    ("IBKR-TFSA", AccountType.TFSA),
    ("WS-TFSA", AccountType.TFSA),
    ("IBKR-RRSP", AccountType.RRSP),
    ("QT-TFSA", AccountType.TFSA),
    ("QT-RRSP", AccountType.RRSP),
]


@pytest.mark.parametrize(("account", "expected"), ACCOUNT_NAMES)
def test_account_names_resolve_without_config(
    temp_ctx: TempContext,
    account: str,
    expected: AccountType,
) -> None:
    with temp_ctx():
        assert resolve_account(account).account_type is expected


@pytest.mark.parametrize(
    ("account", "expected"),
    [
        ("WSTFSA", AccountType.TFSA),  # no separator: caught by substring scan
        ("MY_RRSP_ACCOUNT", AccountType.RRSP),
        ("Questrade Non-Registered", AccountType.NON_REGISTERED),
        ("SOMEBROKER-FHSA", AccountType.FHSA),
        ("BROKER-CASH", AccountType.NON_REGISTERED),
        ("MYSTERY", AccountType.UNKNOWN),
    ],
)
def test_naming_variants(
    temp_ctx: TempContext,
    account: str,
    expected: AccountType,
) -> None:
    with temp_ctx():
        assert resolve_account(account).account_type is expected


def test_right_to_left_token_scan_wins(temp_ctx: TempContext) -> None:
    """A broker code that collides with a type must not shadow the real one."""
    with temp_ctx():
        assert resolve_account("CASH-RRSP").account_type is AccountType.RRSP


def test_config_override_beats_the_naming_convention(temp_ctx: TempContext) -> None:
    overrides = {"accounts": {"map": {"IBKR-TFSA": "RRSP"}}}
    with temp_ctx(overrides):
        assert resolve_account("IBKR-TFSA").account_type is AccountType.RRSP


def test_override_resolves_a_name_the_convention_cannot(temp_ctx: TempContext) -> None:
    overrides = {"accounts": {"map": {"U1234567": {"type": "NON_REGISTERED"}}}}
    with temp_ctx(overrides):
        assert resolve_account("U1234567").account_type is AccountType.NON_REGISTERED


def test_naming_convention_can_be_switched_off(temp_ctx: TempContext) -> None:
    with temp_ctx({"accounts": {"naming_convention": False}}):
        assert resolve_account("IBKR-TFSA").account_type is AccountType.UNKNOWN


def test_the_cache_does_not_leak_between_configs(temp_ctx: TempContext) -> None:
    """The lookup is memoised on the overrides, so a new config is never stale."""
    with temp_ctx():
        assert resolve_account("IBKR-TFSA").account_type is AccountType.TFSA
    with temp_ctx({"accounts": {"map": {"IBKR-TFSA": "RRSP"}}}):
        assert resolve_account("IBKR-TFSA").account_type is AccountType.RRSP


@pytest.mark.parametrize(
    ("account", "overrides"),
    [
        ("QT-MARGIN", {}),
        ("U7654321", {"accounts": {"map": {"U7654321": "MARGIN"}}}),
        ("U7654321", {"accounts": {"map": {"U7654321": {"type": "margin"}}}}),
    ],
)
def test_a_margin_account_is_a_non_registered_one_that_may_borrow(
    temp_ctx: TempContext,
    account: str,
    overrides: dict[str, object],
) -> None:
    with temp_ctx(overrides):
        assert resolve_account(account) == Account(
            account,
            AccountType.NON_REGISTERED,
            margin=True,
        )


def test_a_plain_non_registered_account_is_not_margin(temp_ctx: TempContext) -> None:
    with temp_ctx():
        assert not resolve_account("WS-PERSONAL").margin


def test_fee_rules_default_to_auto(temp_ctx: TempContext) -> None:
    with temp_ctx():
        assert resolve_account("IBKR-PERSONAL").fees == FeeRules()


def test_fee_rules_come_from_the_accounts_map(temp_ctx: TempContext) -> None:
    overrides = {
        "accounts": {
            "map": {
                "QT-TFSA": {
                    "type": "TFSA",
                    "amount_includes_fees": True,
                    "fee_sign": "negative",
                },
                "IBKR-PERSONAL": {
                    "type": "NON_REGISTERED",
                    "amount_includes_fees": False,
                },
            },
        },
    }
    with temp_ctx(overrides):
        assert resolve_account("QT-TFSA").fees == FeeRules(
            FeeConvention.INCLUDED,
            FeeSign.NEGATIVE,
        )
        # A setting the entry leaves out falls back to the default.
        assert resolve_account("IBKR-PERSONAL").fees == FeeRules(
            FeeConvention.EXCLUDED,
            FeeSign.AUTO,
        )


def test_fee_rule_defaults_can_be_pinned(temp_ctx: TempContext) -> None:
    overrides = {
        "accounts": {
            "defaults": {"amount_includes_fees": True, "fee_sign": "positive"},
        },
    }
    with temp_ctx(overrides):
        assert resolve_account("ANY-TFSA").fees == FeeRules(
            FeeConvention.INCLUDED,
            FeeSign.POSITIVE,
        )


def test_a_changed_fee_setting_is_not_served_from_the_cache(
    temp_ctx: TempContext,
) -> None:
    """The account is cached on its config, so a fee setting must be in the key."""
    with temp_ctx({"accounts": {"defaults": {"fee_sign": "negative"}}}):
        assert resolve_account("ANY-TFSA").fees.sign is FeeSign.NEGATIVE
    with temp_ctx({"accounts": {"defaults": {"fee_sign": "positive"}}}):
        assert resolve_account("ANY-TFSA").fees.sign is FeeSign.POSITIVE
