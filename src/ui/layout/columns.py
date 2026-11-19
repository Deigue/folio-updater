"""Table columns that say how their values read, so fitting shrinks each by format."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from rich.table import Column

from domain import Fmt

if TYPE_CHECKING:
    from typing import Any

    from rich.table import Table

    from exporters.sheet_spec import Col


@dataclass
class FormattedColumn(Column):
    """A Rich column aware of its format.

    Doubles as a column spec

    Attributes:
        fmt: How its values read, which decides how fitting may shrink them.
        key: What a drop order names it by (unique key)
        coarse: A percentage that can spare its second decimal.
    """

    fmt: Fmt = Fmt.TEXT
    key: str = ""
    coarse: bool = False

    def __post_init__(self) -> None:
        """Name the column by its header unless a key was given."""
        if not self.key:
            self.key = str(self.header)


def add_column(table: Table, column: FormattedColumn) -> None:
    """Add a fresh copy of a column to a table, with no cells of its own yet."""
    table.columns.append(replace(column, _index=len(table.columns), _cells=[]))


def fmt_of(column: Column) -> Fmt:
    """Tell how a column's values read: text, unless it is a formatted column."""
    return column.fmt if isinstance(column, FormattedColumn) else Fmt.TEXT


def key_of(column: Column) -> str:
    """Name a column the way a drop order knows it."""
    return column.key if isinstance(column, FormattedColumn) else str(column.header)


def is_coarse(column: Column) -> bool:
    """Tell whether a column is a percentage that can spare its second decimal."""
    return isinstance(column, FormattedColumn) and column.coarse


def formatted(column: Col, **rich: Any) -> FormattedColumn:
    """Describe a catalog column for a terminal table.

    A figure is right-aligned and kept on one line; `rich` passes anything else
    a table wants of it, such as `no_wrap` on a text column.
    """
    if column.fmt is not Fmt.TEXT:
        rich = {"justify": "right", "no_wrap": True, **rich}
    return FormattedColumn(column.header, fmt=column.fmt, coarse=column.coarse, **rich)
