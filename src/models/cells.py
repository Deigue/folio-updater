"""Reading and writing the cells of a stored row.

A row arrives as a mapping of column name to cell. Until every read goes
through the repositories it may come from a pandas frame, where a NULL reads as
NaN, so each reader treats NaN as a missing cell too.

A stored cell is read exactly as it was written. Text from a provider or a
source file, which may carry padding or a "nan", is cleaned where it arrives,
not here.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from domain.numeric import dec

if TYPE_CHECKING:
    from decimal import Decimal


def is_missing(value: object) -> bool:
    """Whether a cell holds nothing: NULL, or the NaN a frame reads NULL as."""
    return value is None or (isinstance(value, float) and math.isnan(value))


def read_text(value: object) -> str | None:
    """Read a nullable text cell exactly as stored."""
    return None if is_missing(value) else str(value)


def read_number(value: object) -> Decimal | None:
    """Read a nullable number cell, keeping a NULL a None rather than a zero."""
    return None if is_missing(value) else dec(value)


def read_moment(value: object) -> datetime | None:
    """Read an ISO-8601 timestamp cell, taking one without a zone as UTC.

    A cell that is not a timestamp reads as None: every timestamp column says
    when something was fetched, and an unknown time only makes it due again.
    """
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
