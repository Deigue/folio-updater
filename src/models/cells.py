"""Reading and writing stored cells exactly as written; a frame's NaN reads as NULL."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from domain.cells import is_blank
from domain.numeric import MISSING_TEXT, dec

if TYPE_CHECKING:
    from decimal import Decimal


def read_text(value: object, *, provider: bool = False) -> str | None:
    """Read a nullable text cell exactly as stored.

    Args:
        value: The cell.
        provider: Read it as a quote provider sends it instead: trimmed, with
            pandas' missing markers ("nan", "None") read as blank.

    Returns:
        The text, or None for a blank cell.
    """
    if is_blank(value):
        return None
    text = str(value)
    if not provider:
        return text
    text = text.strip()
    return None if not text or text.lower() in MISSING_TEXT else text


def read_number(value: object, *, provider: bool = False) -> Decimal | None:
    """Read a nullable number cell, keeping a NULL a None rather than a zero.

    Args:
        value: The cell.
        provider: Read it as a quote provider sends it instead: blank as
            `read_text` reads it, and a zero blank too, since nothing trades at
            zero and a provider sends one for a figure it lacks.

    Returns:
        The number, or None for a blank cell.
    """
    if not provider:
        return None if is_blank(value) else dec(value)
    if read_text(value, provider=True) is None:
        return None
    return dec(value) or None


def read_moment(value: object) -> datetime | None:
    """Read an ISO-8601 timestamp cell as UTC when zoneless, None when unreadable."""
    read = read_text(value)
    if read is None:
        return None
    try:
        parsed = datetime.fromisoformat(read)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def required_text(value: object, column: str) -> str:
    """Read a text cell the row cannot do without.

    Args:
        value: The cell.
        column: Its column, named in the error.

    Returns:
        The cell's text.
    """
    return _present(read_text(value), column)


def required_number(value: object, column: str) -> Decimal:
    """Read a number cell the row cannot do without.

    Args:
        value: The cell.
        column: Its column, named in the error.

    Returns:
        The cell's number.
    """
    return _present(read_number(value), column)


def required_moment(value: object, column: str) -> datetime:
    """Read a timestamp cell the row cannot do without.

    Args:
        value: The cell.
        column: Its column, named in the error.

    Returns:
        The cell's timestamp.
    """
    return _present(read_moment(value), column)


def required_flag(value: object, column: str) -> bool:
    """Read a 0/1 flag cell, which a frame may hand over as a float.

    Args:
        value: The cell.
        column: Its column, named in the error.

    Returns:
        The flag.
    """
    return bool(int(required_number(value, column)))


def stored_number(value: Decimal | None) -> str | None:
    """Write a number cell as exact decimal text; SQLite gives it the column's type."""
    return None if value is None else format(value, "f")


def stored_moment(value: datetime | None) -> str | None:
    """Write a timestamp cell as ISO-8601 text."""
    return None if value is None else value.isoformat()


def _present[T](read: T | None, column: str) -> T:
    """Hand back a cell the row cannot do without.

    Raises:
        ValueError: If the cell is empty.
    """
    if read is None:
        msg = f"Stored row has no {column}"
        raise ValueError(msg)
    return read
