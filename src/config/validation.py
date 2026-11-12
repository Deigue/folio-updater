"""Validators that turn a value written in `config.yaml` into a usable one.

A validator takes the raw value and returns the usable value, or an `Unusable`
when nothing of it can be used, so the setting keeps its default. An `Unusable`
with a reason is reported, while one without is dropped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from config.optional_fields import FieldType
from domain import (
    MARGIN_MARKER,
    ROOM_BEARING_TYPES,
    SUPPORTED_CURRENCIES,
    AccountType,
    FeeConvention,
    FeeSign,
    parse_account_type,
)
from domain.numeric import dec
from term import console_warning

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping


class ConfigError(ValueError):
    """The config file cannot be used as written, so the command stops."""


@dataclass(frozen=True)
class Unusable:
    """Nothing of a written value can be used.

    Attributes:
        reason: Why, as a phrase following the value ("is not a year"), or
            empty when it has already been reported.
    """

    reason: str = ""


REPORTED = Unusable()
NOT_A_MAPPING = Unusable("is not a mapping of settings")

type Validator = Callable[[Any, str], Any]

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

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def report_unusable(
    path: str,
    written: object,
    unusable: Unusable,
    fallback: str,
) -> None:
    """Warn that a written value is not used, unless that was already reported.

    Args:
        path: The setting's dotted path.
        written: The value as written.
        unusable: Why it cannot be used.
        fallback: What happens instead, such as "using 15" or "ignoring it".
    """
    if unusable.reason:
        console_warning(f"{path}: {written} {unusable.reason}; {fallback}.")


def report_unknown(path: str) -> None:
    """Warn that a key names no setting."""
    console_warning(f"{path}: unknown setting, ignoring it.")


def _listed(choices: Iterable[str]) -> str:
    """Join choices as `a, b or c`."""
    *rest, last = choices
    return f"{', '.join(rest)} or {last}" if rest else last


def _is_scalar(value: object) -> bool:
    """Whether a value is a string or a number, which YAML booleans are not."""
    return isinstance(value, str | int | float) and not isinstance(value, bool)


# -- SCALARS ------------------------------------------------------------------


def as_written(value: Any, _path: str) -> Any:  # noqa: ANN401
    """Keep the value as written, for a block whose own parser judges it."""
    return value


def text(value: Any, _path: str) -> Any:  # noqa: ANN401
    """Accept a string or a number, as a string."""
    return str(value) if _is_scalar(value) else Unusable("is not text")


def boolean(value: Any, _path: str) -> Any:  # noqa: ANN401
    """Accept a YAML boolean."""
    return value if isinstance(value, bool) else Unusable("is not true or false")


def positive_int(value: Any, _path: str) -> Any:  # noqa: ANN401
    """Accept a whole number above zero."""
    # bool is a subclass of int, so `true` must not read as 1.
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return Unusable("is not a positive whole number")


def log_level(value: Any, _path: str) -> Any:  # noqa: ANN401
    """Accept a logging level name, in any case."""
    level = str(value).upper()
    if level in logging.getLevelNamesMapping():
        return level
    return Unusable(f"is not a log level ({_listed(_LOG_LEVELS)})")


def currency(value: Any, _path: str) -> Any:  # noqa: ANN401
    """Accept a currency the app can value in, in any case."""
    code = str(value).upper()
    if code in SUPPORTED_CURRENCIES:
        return code
    return Unusable(f"is not {_listed(sorted(SUPPORTED_CURRENCIES))}")


def one_of(spellings: Mapping[str, object]) -> Validator:
    """Accept a value whose lower-cased spelling is one of `spellings`."""
    unusable = Unusable(f"is not one of {', '.join(spellings)}")

    def validate(value: Any, _path: str) -> Any:  # noqa: ANN401
        spelling = str(value).lower()
        return spelling if spelling in spellings else unusable

    return validate


def names(value: Any, _path: str) -> Any:  # noqa: ANN401
    """Accept a list of names, or one name as a list of one.

    Each name is trimmed, and an empty entry (a stray `-`) is dropped.
    """
    if _is_scalar(value):
        value = [value]
    if not isinstance(value, list):
        return Unusable("is not a name or a list of names")
    trimmed = (str(entry).strip() for entry in value if entry is not None)
    return [name for name in trimmed if name]


# -- COMBINATORS --------------------------------------------------------------


def fields(
    *,
    shorthand: str | None = None,
    strict: bool = False,
    finish: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    **validators: Validator,
) -> Validator:
    """Accept a mapping of named fields, each checked by its own validator.

    A field left empty is not configured, and an unknown field is reported.

    Args:
        shorthand: The field a lone value stands for, so `TFSA` reads as
            `{type: TFSA}`.
        strict: Whether an unusable field makes the whole entry unusable, rather
            than only being dropped.
        finish: Applied to the usable fields, for a rule spanning several.
        **validators: The validator of each field.

    Returns:
        The validator. Its result holds the usable fields only.
    """

    def validate(value: Any, path: str) -> Any:  # noqa: ANN401
        written_short = shorthand is not None and not isinstance(value, dict)
        if written_short:
            value = {shorthand: value}
        if not isinstance(value, dict):
            return NOT_A_MAPPING
        usable: dict[str, Any] = {}
        for name, entry in value.items():
            if name not in validators:
                report_unknown(f"{path}.{name}")
                continue
            if entry is None:
                continue
            # A lone value stands for the whole entry, so it is named by the entry.
            field_path = path if written_short else f"{path}.{name}"
            result = validators[name](entry, field_path)
            if isinstance(result, Unusable):
                entry_name = path.rsplit(".", 1)[-1]
                fallback = f"ignoring {entry_name}" if strict else "ignoring it"
                report_unusable(field_path, entry, result, fallback)
                if strict:
                    return REPORTED
                continue
            usable[name] = result
        return finish(usable) if finish else usable

    return validate


def map_of(key: Validator, value: Validator, *, clash_stops: bool = False) -> Validator:
    """Accept a mapping keyed by data, such as account names or years.

    An entry left empty is dropped, and so is one whose key or value cannot be
    used, with a warning.

    Args:
        key: Normalises each key.
        value: Checks each value.
        clash_stops: Whether two keys normalising to one with different values
            stop the load, since either could be the one meant.

    Returns:
        The validator.
    """

    def validate(raw: Any, path: str) -> Any:  # noqa: ANN401
        if not isinstance(raw, dict):
            return NOT_A_MAPPING
        usable: dict[Any, Any] = {}
        written_as: dict[Any, Any] = {}
        for written, entry in raw.items():
            if entry is None:
                continue
            name = key(written, path)
            if isinstance(name, Unusable):
                console_warning(f"{path}: ignoring {written}, which {name.reason}.")
                continue
            entry_path = f"{path}.{written}"
            result = value(entry, entry_path)
            if isinstance(result, Unusable):
                report_unusable(entry_path, entry, result, "ignoring it")
                continue
            if clash_stops and name in usable and usable[name] != result:
                msg = (
                    f"{path}: {written_as[name]} and {written} name the same "
                    "entry with different settings. Keep one of them."
                )
                raise ConfigError(msg)
            usable[name] = result
            written_as[name] = written
        return usable

    return validate


def non_empty(validate: Validator) -> Validator:
    """Wrap `validate` so an empty result is dropped.

    Whatever emptied it was reported where it happened, or was left empty.
    """

    def check(value: Any, path: str) -> Any:  # noqa: ANN401
        result = validate(value, path)
        return result if isinstance(result, Unusable) or result else REPORTED

    return check


# -- FOLIO VALUES -------------------------------------------------------------


def account_name(value: Any, _path: str) -> Any:  # noqa: ANN401
    """Read an account name, upper-cased so a lookup never depends on YAML casing."""
    if isinstance(value, bool):
        return Unusable(
            "YAML read as a boolean (an unquoted ON, OFF, YES or NO). "
            'Quote the name (for example "ON")',
        )
    return str(value).strip().upper()


def account_type(value: Any, _path: str) -> Any:  # noqa: ANN401
    """Read an account type or one of its aliases, or `MARGIN`."""
    if str(value).upper() == MARGIN_MARKER:
        return MARGIN_MARKER
    return parse_account_type(str(value)) or Unusable("is not an account type")


def expand_margin(entry: dict[str, Any]) -> dict[str, Any]:
    """Read a `MARGIN` type as a non-registered account that trades on margin."""
    if entry.get("type") == MARGIN_MARKER:
        return {**entry, "type": AccountType.NON_REGISTERED, "margin": True}
    return entry


def room_type(value: Any, _path: str) -> Any:  # noqa: ANN401
    """Read an account type with yearly contribution room."""
    parsed = parse_account_type(str(value))
    if parsed is None:
        return Unusable("is not an account type")
    if parsed not in ROOM_BEARING_TYPES:
        return Unusable(
            "has no yearly contribution room (only TFSA, RRSP and FHSA do)",
        )
    return parsed


def year(value: Any, _path: str) -> Any:  # noqa: ANN401
    """Read a calendar year."""
    try:
        return int(str(value).strip())
    except ValueError:
        return Unusable("is not a year")


def money(value: Any, _path: str) -> Any:  # noqa: ANN401
    """Read an amount of money."""
    try:
        return dec(float(value))
    except (TypeError, ValueError):
        return Unusable("is not an amount")


def field_type(value: Any, _path: str) -> Any:  # noqa: ANN401
    """Read an optional column's data type."""
    try:
        return FieldType(str(value).lower()).value
    except ValueError:
        return Unusable(f"is not one of {', '.join(kind.value for kind in FieldType)}")
