"""Column declarations a model's fields carry, which `db` renders into its table."""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dataclasses import Field

    from _typeshed import DataclassInstance

_STORED = "stored"
_FLATTENED = "flattened"


class Kind(StrEnum):
    """What a column holds."""

    TEXT = "text"
    NUMBER = "number"
    INTEGER = "integer"
    FLAG = "flag"  # 0 or 1: SQLite has no boolean type
    DATE = "date"  # `YYYY-MM-DD` text: SQLite has no date type


@dataclass(frozen=True)
class StoredColumn:
    """One column of a stored table.

    Attributes:
        name: The column's name.
        kind: What it holds.
        required: Whether every row must have it.
        key: Whether it is (part of) the primary key.
        autoincrement: Whether SQLite numbers it, never reusing a number.
        upper: Whether it is held in upper case.
        non_empty: Whether empty text is refused, so only NULL means "none".
    """

    name: str
    kind: Kind = Kind.TEXT
    required: bool = False
    key: bool = False
    autoincrement: bool = False
    upper: bool = False
    non_empty: bool = False


def stored(name: str, kind: Kind = Kind.TEXT, **switches: bool) -> dict[str, object]:
    """Return the field metadata declaring the column a field is stored in."""
    return {_STORED: StoredColumn(str(name), kind, **switches)}


def flattened(model: type[DataclassInstance]) -> dict[str, object]:
    """Return the field metadata of a value stored as the columns of `model`."""
    return {_FLATTENED: model}


def column_of(item: Field[object]) -> StoredColumn:
    """Return the column a stored field is held in."""
    return item.metadata[_STORED]


def stored_columns(model: type[DataclassInstance]) -> tuple[StoredColumn, ...]:
    """Return a model's columns in table order, the key first."""
    columns = _declared(model)
    return tuple(c for c in columns if c.key) + tuple(c for c in columns if not c.key)


def columns_of_kind(model: type[DataclassInstance], kind: Kind) -> tuple[str, ...]:
    """Name a model's columns that hold `kind`, in table order."""
    return tuple(c.name for c in stored_columns(model) if c.kind is kind)


def _declared(model: type[DataclassInstance]) -> list[StoredColumn]:
    """Collect the columns a model's fields declare, in field order."""
    columns: list[StoredColumn] = []
    for item in fields(model):
        if _STORED in item.metadata:
            columns.append(item.metadata[_STORED])
        elif _FLATTENED in item.metadata:
            columns.extend(_declared(item.metadata[_FLATTENED]))
    return columns
