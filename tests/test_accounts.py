"""Tests for inferring an account's tax type and fee convention from its name."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from engine.accounts import fee_convention_for, is_taxable, resolve_account_type
from utils.constants import AccountType, FeeConvention

if TYPE_CHECKING:
    from .test_types import TempContext

LIVE_ACCOUNTS = [
    ("IBKR-PERSONAL", AccountType.NON_REGISTERED),
    ("WS-PERSONAL", AccountType.NON_REGISTERED),
    ("IBKR-TFSA", AccountType.TFSA),
    ("WS-TFSA", AccountType.TFSA),
    ("IBKR-RRSP", AccountType.RRSP),
    ("QT-TFSA", AccountType.TFSA),
    ("QT-RRSP", AccountType.RRSP),
]


@pytest.mark.parametrize(("account", "expected"), LIVE_ACCOUNTS)
def test_live_accounts_resolve_without_config(
    temp_ctx: TempContext,
    account: str,
    expected: AccountType,
) -> None:
    with temp_ctx():
        assert resolve_account_type(account) is expected


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
        assert resolve_account_type(account) is expected


def test_right_to_left_token_scan_wins(temp_ctx: TempContext) -> None:
    """A broker code that collides with a type must not shadow the real one."""
    with temp_ctx():
        assert resolve_account_type("CASH-RRSP") is AccountType.RRSP


def test_config_override_beats_the_naming_convention(temp_ctx: TempContext) -> None:
    overrides = {"accounts": {"map": {"IBKR-TFSA": "RRSP"}}}
    with temp_ctx(overrides):
        assert resolve_account_type("IBKR-TFSA") is AccountType.RRSP


def test_override_resolves_a_name_the_convention_cannot(temp_ctx: TempContext) -> None:
    overrides = {"accounts": {"map": {"U1234567": {"type": "NON_REGISTERED"}}}}
    with temp_ctx(overrides):
        assert resolve_account_type("U1234567") is AccountType.NON_REGISTERED


def test_naming_convention_can_be_switched_off(temp_ctx: TempContext) -> None:
    with temp_ctx({"accounts": {"naming_convention": False}}):
        assert resolve_account_type("IBKR-TFSA") is AccountType.UNKNOWN


def test_the_cache_does_not_leak_between_configs(temp_ctx: TempContext) -> None:
    """The lookup is memoised on the overrides, so a new config is never stale."""
    with temp_ctx():
        assert resolve_account_type("IBKR-TFSA") is AccountType.TFSA
    with temp_ctx({"accounts": {"map": {"IBKR-TFSA": "RRSP"}}}):
        assert resolve_account_type("IBKR-TFSA") is AccountType.RRSP


@pytest.mark.parametrize(
    ("account", "expected"),
    [
        ("IBKR-PERSONAL", True),
        ("WS-PERSONAL", True),
        ("IBKR-TFSA", False),
        ("IBKR-RRSP", False),
        ("MYSTERY", False),
    ],
)
def test_is_taxable(temp_ctx: TempContext, account: str, *, expected: bool) -> None:
    with temp_ctx():
        assert is_taxable(account) is expected


def test_fee_convention_defaults_to_auto(temp_ctx: TempContext) -> None:
    with temp_ctx():
        assert fee_convention_for("IBKR-PERSONAL") is FeeConvention.AUTO


def test_fee_convention_override(temp_ctx: TempContext) -> None:
    overrides = {
        "accounts": {
            "map": {
                "QT-TFSA": {"type": "TFSA", "amount_includes_fees": True},
                "IBKR-PERSONAL": {
                    "type": "NON_REGISTERED",
                    "amount_includes_fees": False,
                },
            },
        },
    }
    with temp_ctx(overrides):
        assert fee_convention_for("QT-TFSA") is FeeConvention.INCLUDED
        assert fee_convention_for("IBKR-PERSONAL") is FeeConvention.EXCLUDED


def test_fee_convention_default_can_be_pinned(temp_ctx: TempContext) -> None:
    overrides = {"accounts": {"defaults": {"amount_includes_fees": True}}}
    with temp_ctx(overrides):
        assert fee_convention_for("ANY-TFSA") is FeeConvention.INCLUDED
