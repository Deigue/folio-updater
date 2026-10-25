"""Check a loaded `config.yaml` against what each setting may hold.

Each validator reads its block from the raw `settings` and writes the usable
result into `validated`, which starts as a copy of the defaults. A value of the
wrong type leaves the default in place rather than failing the command.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from domain import (
    ROOM_BEARING_TYPES,
    SUPPORTED_CURRENCIES,
    AccountType,
    Column,
    FeeConvention,
)
from domain.numeric import dec
from term import console_warning

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from decimal import Decimal

# How `amount_includes_fees` is spelled in YAML.
FEE_CONVENTIONS: dict[str, FeeConvention] = {
    "auto": FeeConvention.AUTO,
    "true": FeeConvention.INCLUDED,
    "yes": FeeConvention.INCLUDED,
    "included": FeeConvention.INCLUDED,
    "false": FeeConvention.EXCLUDED,
    "no": FeeConvention.EXCLUDED,
    "excluded": FeeConvention.EXCLUDED,
}

# Keys copied straight from YAML once they type-check, mapped to the type they
# must have and the coercion applied before storing. `object` accepts any
# non-null value. Blocks needing per-field rules get their own `_validate_*`
# helper instead.
_PASSTHROUGH_KEYS: dict[str, tuple[type, Callable[[Any], Any]]] = {
    "folio_path": (object, str),
    "data_path": (object, str),
    "header_ignore": (list, list),
    "brokers": (dict, dict),
    "optional_columns": (dict, dict),
    "transforms": (dict, dict),
}


def validate_config(
    settings: dict[str, Any],
    defaults: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the loaded configuration against expected structure and values.

    Args:
        settings: Raw configuration dictionary to validate
        defaults: The setting values to fall back on

    Returns:
        Validated configuration
    """
    validated: dict[str, Any] = deepcopy(dict(defaults))

    _validate_passthrough_keys(settings, validated)
    _validate_log_level(settings, validated)
    _validate_sheets(settings, validated)
    _validate_header_keywords(settings, validated)
    _validate_duplicate_approval(settings, validated)
    _validate_backup(settings, validated)
    _validate_accounts(settings, validated)
    _validate_cost_basis(settings, validated)
    _validate_display(settings, validated)
    _validate_checks(settings, validated)
    _validate_quotes(settings, validated)
    _validate_contribution_room(settings, validated)

    return validated


def _validate_passthrough_keys(
    settings: dict[str, Any],
    validated: dict[str, Any],
) -> None:
    """Copy every `_PASSTHROUGH_KEYS` entry that type-checks.

    Values of the wrong type, and explicit nulls, leave the default in
    place.
    """
    for key, (expected, coerce) in _PASSTHROUGH_KEYS.items():
        value = settings.get(key)
        if value is not None and isinstance(value, expected):
            validated[key] = coerce(value)


def _validate_accounts(
    settings: dict[str, Any],
    validated: dict[str, Any],
) -> None:
    """Validate the `accounts` block, normalising both `map` forms."""
    accounts = settings.get("accounts")
    if not isinstance(accounts, dict):
        return

    current = validated["accounts"]
    if isinstance(accounts.get("naming_convention"), bool):
        current["naming_convention"] = accounts["naming_convention"]

    defaults = accounts.get("defaults")
    if isinstance(defaults, dict) and "amount_includes_fees" in defaults:
        # `false` is a meaningful value here, so test for the key.
        fees = str(defaults["amount_includes_fees"]).lower()
        if fees in FEE_CONVENTIONS:
            current["defaults"] = {"amount_includes_fees": fees}

    raw_map = accounts.get("map")
    if isinstance(raw_map, dict):
        current["map"] = {
            name: entry
            for name, entry in (
                (str(name), _account_entry(value)) for name, value in raw_map.items()
            )
            if entry is not None
        }


def _account_entry(value: Any) -> dict[str, Any] | None:  # noqa: ANN401
    """Normalise one `accounts.map` entry, or None when it is unusable."""
    entry = value if isinstance(value, dict) else {"type": value}
    account_type = entry.get("type")
    normalized: dict[str, Any] = {}

    if account_type is not None:
        try:
            normalized["type"] = AccountType(str(account_type).upper())
        except ValueError:
            return None

    if "amount_includes_fees" in entry:
        fees = str(entry["amount_includes_fees"]).lower()
        if fees in FEE_CONVENTIONS:
            normalized["amount_includes_fees"] = fees

    return normalized or None


def _validate_cost_basis(
    settings: dict[str, Any],
    validated: dict[str, Any],
) -> None:
    """Validate the `cost_basis` block."""
    cost_basis = settings.get("cost_basis")
    if not isinstance(cost_basis, dict):
        return
    if isinstance(cost_basis.get("auto_getfx"), bool):
        validated["cost_basis"]["auto_getfx"] = cost_basis["auto_getfx"]


def _validate_checks(
    settings: dict[str, Any],
    validated: dict[str, Any],
) -> None:
    """Validate the `checks` block."""
    checks = settings.get("checks")
    if not isinstance(checks, dict):
        return
    current = validated["checks"]
    for key in ("disabled", "ignore_tickers", "ignore_accounts"):
        value = checks.get(key)
        if isinstance(value, list):
            current[key] = [str(entry).strip() for entry in value if entry]


def _validate_quotes(
    settings: dict[str, Any],
    validated: dict[str, Any],
) -> None:
    """Validate the `quotes` block, keeping the default on any bad value."""
    quotes = settings.get("quotes")
    if not isinstance(quotes, dict):
        return

    current = validated["quotes"]
    for key in (
        "ttl_minutes",
        "metadata_ttl_days",
        "fundamentals_ttl_hours",
        "timeout_seconds",
    ):
        value = quotes.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            current[key] = value

    overrides = quotes.get("symbol_overrides")
    if isinstance(overrides, dict):
        current["symbol_overrides"] = {
            str(key): str(value) for key, value in overrides.items() if key and value
        }


def _validate_contribution_room(
    settings: dict[str, Any],
    validated: dict[str, Any],
) -> None:
    """Validate `contribution_room`, dropping anything unresolvable.

    Keys are account types, then calendar years. A type the engine does not
    know, or a year that is not a number, is skipped. A known type without
    yearly room (an RESP's limit is lifetime and per beneficiary) is skipped
    with a warning, since the user meant it and would otherwise never learn
    that it is ignored.
    """
    room = settings.get("contribution_room")
    if not isinstance(room, dict):
        return

    resolved: dict[AccountType, dict[int, Decimal]] = {}
    for name, years in room.items():
        if not isinstance(years, dict):
            continue
        try:
            account_type = AccountType(str(name).strip().upper())
        except ValueError:
            continue
        if account_type not in ROOM_BEARING_TYPES:
            console_warning(
                f"contribution_room: ignoring {account_type}, which has no "
                "yearly contribution room (only TFSA, RRSP and FHSA do).",
            )
            continue
        limits = _room_years(years)
        if limits:
            resolved[account_type] = limits

    validated["contribution_room"] = resolved


def _room_years(years: dict[Any, Any]) -> dict[int, Decimal]:
    """Read one account type's year-to-limit mapping."""
    limits: dict[int, Decimal] = {}
    for year, limit in years.items():
        try:
            limits[int(str(year).strip())] = dec(float(limit))
        except (TypeError, ValueError):
            continue
    return limits


def _validate_display(
    settings: dict[str, Any],
    validated: dict[str, Any],
) -> None:
    """Validate the `display` block."""
    display = settings.get("display")
    if not isinstance(display, dict):
        return
    currency = str(display.get("currency", "")).upper()
    if currency in SUPPORTED_CURRENCIES:
        validated["display"] = {"currency": currency}


def _validate_log_level(
    settings: dict[str, Any],
    validated: dict[str, Any],
) -> None:
    """Validate `log_level`, falling back to the default on any bad value."""
    log_level = str(settings.get("log_level", validated["log_level"])).upper()
    if log_level not in logging.getLevelNamesMapping():
        log_level = validated["log_level"]
    validated["log_level"] = log_level


def _validate_sheets(settings: dict[str, Any], validated: dict[str, Any]) -> None:
    """Validate `sheets`, keeping only the known sheet names."""
    sheets = settings.get("sheets")
    if not isinstance(sheets, dict):
        return
    validated["sheets"].update(
        {
            name: str(value)
            for name, value in sheets.items()
            if name in validated["sheets"] and value is not None
        },
    )


def _validate_header_keywords(
    settings: dict[str, Any],
    validated: dict[str, Any],
) -> None:
    """Validate `header_keywords`, dropping mappings for unknown columns."""
    header_keywords = settings.get("header_keywords")
    if not isinstance(header_keywords, dict):
        return
    validated["header_keywords"].update(
        {
            column: list(keywords)
            for column, keywords in header_keywords.items()
            # Add internal fields that are not in the default mapping here.
            if isinstance(keywords, list)
            and (
                column in validated["header_keywords"] or column == str(Column.Txn.FEE)
            )
        },
    )


def _validate_duplicate_approval(
    settings: dict[str, Any],
    validated: dict[str, Any],
) -> None:
    """Validate the `duplicate_approval` block."""
    duplicate_approval = settings.get("duplicate_approval")
    if not isinstance(duplicate_approval, dict):
        return
    current = validated["duplicate_approval"]
    for key in ("column_name", "approval_value"):
        if duplicate_approval.get(key) is not None:
            current[key] = str(duplicate_approval[key])


def _validate_backup(
    settings: dict[str, Any],
    validated: dict[str, Any],
) -> None:
    """Validate the `backup` block."""
    backup = settings.get("backup")
    if not isinstance(backup, dict):
        return
    current = validated["backup"]

    if isinstance(backup.get("enabled"), bool):
        current["enabled"] = backup["enabled"]

    if backup.get("path") is not None:
        current["path"] = str(backup["path"])

    max_backups = backup.get("max_backups")
    # bool is a subclass of int, so `max_backups: true` must not read as 1.
    if (
        isinstance(max_backups, int)
        and not isinstance(max_backups, bool)
        and max_backups > 0
    ):
        current["max_backups"] = max_backups
