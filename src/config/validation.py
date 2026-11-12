"""Check a loaded `config.yaml` against what each setting may hold.

Each validator reads its block from the raw `settings` and writes the usable
result into `validated`, which starts as a copy of the defaults. A value of the
wrong type leaves the default in place rather than failing the command.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from config.optional_fields import OptionalField
from domain import (
    MARGIN_MARKER,
    ROOM_BEARING_TYPES,
    SUPPORTED_CURRENCIES,
    AccountType,
    Column,
    FeeConvention,
    FeeSign,
    parse_account_type,
)
from domain.numeric import dec
from term import console_warning

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from decimal import Decimal


class ConfigError(ValueError):
    """The config file cannot be used as written, so the command stops."""


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

# How `fee_sign` is spelled in YAML.
FEE_SIGNS: dict[str, FeeSign] = {sign.value: sign for sign in FeeSign}

# The fee settings an account, or `defaults`, may carry, and what each may be spelled.
_FEE_SPELLINGS: dict[str, Mapping[str, object]] = {
    "amount_includes_fees": FEE_CONVENTIONS,
    "fee_sign": FEE_SIGNS,
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
    _validate_log_level(settings, validated, "log_level")
    _validate_log_level(settings, validated, "audit_log_level")
    _validate_sheets(settings, validated)
    _validate_header_keywords(settings, validated)
    _validate_optional_columns(settings, validated)
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
    accounts = _mapping_block(settings, "accounts")
    if accounts is None:
        return

    current = validated["accounts"]
    if isinstance(accounts.get("naming_convention"), bool):
        current["naming_convention"] = accounts["naming_convention"]

    defaults = accounts.get("defaults")
    if isinstance(defaults, dict):
        # Each default is set on its own, so pinning one leaves the other as it was.
        # `false` is a meaningful convention, so test for the key.
        for key, spellings in _FEE_SPELLINGS.items():
            if key in defaults and str(defaults[key]).lower() in spellings:
                current["defaults"][key] = str(defaults[key]).lower()

    raw_map = _mapping_block(accounts, "map", "accounts.map")
    if raw_map is not None:
        current["map"] = _account_map(raw_map)


def _account_map(raw_map: dict[Any, Any]) -> dict[str, dict[str, Any]]:
    """Normalise `accounts.map`, keyed by the upper-cased account name.

    An entry with nothing usable is dropped with a warning. Two keys that differ
    only in case and disagree stop the load, since either could be the one meant.

    Raises:
        ConfigError: When two keys name one account with different settings.
    """
    resolved: dict[str, dict[str, Any]] = {}
    written_as: dict[str, str] = {}
    for key, value in raw_map.items():
        if isinstance(key, bool):
            console_warning(
                f"accounts.map: ignoring {key}, which YAML read as a boolean "
                '(an unquoted ON, OFF, YES or NO). Quote the name (for example "ON").',
            )
            continue
        name = str(key).strip().upper()
        entry = _account_entry(value)
        if entry is None:
            console_warning(
                f"accounts.map: ignoring {key}, which has no usable type or "
                "fee setting.",
            )
            continue
        if name in resolved and resolved[name] != entry:
            msg = (
                f"accounts.map: {written_as[name]} and {key} name the same "
                "account with different settings. Keep one of them."
            )
            raise ConfigError(msg)
        resolved[name] = entry
        written_as[name] = str(key)
    return resolved


def _account_entry(value: Any) -> dict[str, Any] | None:  # noqa: ANN401
    """Normalise one `accounts.map` entry, or None when it is unusable."""
    entry = value if isinstance(value, dict) else {"type": value}
    account_type = entry.get("type")
    normalized: dict[str, Any] = {}

    if account_type is not None:
        if str(account_type).upper() == MARGIN_MARKER:
            normalized["type"] = AccountType.NON_REGISTERED
            normalized["margin"] = True
        else:
            parsed = parse_account_type(str(account_type))
            if parsed is None:
                return None
            normalized["type"] = parsed

    for key, spellings in _FEE_SPELLINGS.items():
        if key in entry and str(entry[key]).lower() in spellings:
            normalized[key] = str(entry[key]).lower()

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
        account_type = parse_account_type(str(name))
        if account_type is None:
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
    key: str,
) -> None:
    """Validate one log level setting, falling back to its default on a bad value."""
    level = str(settings.get(key, validated[key])).upper()
    if level not in logging.getLevelNamesMapping():
        level = validated[key]
    validated[key] = level


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


def _validate_optional_columns(
    settings: dict[str, Any],
    validated: dict[str, Any],
) -> None:
    """Validate `optional_columns`, skipping each entry that cannot be used."""
    columns = _mapping_block(settings, "optional_columns")
    if columns is None:
        return
    usable: dict[str, Any] = {}
    for name, entry in columns.items():
        try:
            OptionalField.from_config_entry(str(name), entry)
        except ValueError as error:
            console_warning(f"optional_columns: ignoring {name}. {error}")
        else:
            usable[str(name)] = entry
    validated["optional_columns"] = usable


def _mapping_block(
    container: Mapping[str, Any],
    key: str,
    label: str | None = None,
) -> dict[str, Any] | None:
    """Read one settings block that must be a mapping.

    An absent key is simply not configured. A key that is present but empty (a
    bare `map:`) or holds a list or scalar is lost, so it is reported.

    Args:
        container: The mapping the block sits in.
        key: The block's key.
        label: How the block is named in the warning, when not just `key`.

    Returns:
        The block, or None when it is absent or unusable.
    """
    if key not in container:
        return None
    block = container[key]
    if isinstance(block, dict):
        return block
    console_warning(
        f"{label or key}: ignoring it, because it must hold a mapping of settings.",
    )
    return None


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
