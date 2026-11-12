"""Tidying text the way identifiers are compared: trimmed and upper-cased."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


def strip_upper(value: str) -> str:
    """Trim a value and upper-case it."""
    return value.strip().upper()


def unique_upper(values: Iterable[str]) -> list[str]:
    """Trim and upper-case each value, keeping the first of any that then repeat.

    Returns:
        The values, each once, in the order given.
    """
    return list(dict.fromkeys(strip_upper(value) for value in values))
