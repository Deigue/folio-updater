"""The shape of an exported table, independent of the file it lands in."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class Fmt(StrEnum):
    """How one column's values should read."""

    TEXT = "text"
    ID = "id"
    DATE = "date"
    MONEY = "money"
    MONEY_SIGNED = "money_signed"
    PRICE = "price"
    PRICE_SIGNED = "price_signed"
    UNITS = "units"
    PERCENT = "percent"
    PERCENT_SIGNED = "percent_signed"
    RATE = "rate"


class Role(StrEnum):
    """What kind of line a row is, which decides how it is drawn."""

    DATA = "data"
    MUTED = "muted"  # reference data rather than performance
    CLOSED = "closed"  # a position that is no longer held
    BLANK = "blank"  # a spacer, and the end of the filtered range
    SUBTOTAL = "subtotal"
    TOTAL = "total"


@dataclass(frozen=True)
class Col:
    """One column of a table.

    Attributes:
        header: The heading, which is also the key rows are built from.
        fmt: How the column's values read.
        group: A band drawn above a run of columns sharing the same name.
        width: An explicit width, in characters, when measuring the content
            would get it wrong.
        bar: Draw a bar behind the value, sized by the value itself. A CSV,
            having no cell to draw in, just writes the figure.
        quiet: Leave a zero blank. For a column where a zero says nothing, such
            as the price on a cash row or a dividend never paid, where a column
            of `0.00` is noise a reader has to look past.
    """

    header: str
    fmt: Fmt = Fmt.TEXT
    group: str | None = None
    width: int | None = None
    bar: bool = False
    quiet: bool = False


@dataclass(frozen=True)
class Row:
    """One line of a table, with one cell per column.

    Attributes:
        cells: Values in column order. `None` is a genuine blank.
        role: What kind of line this is.
    """

    cells: tuple[object, ...]
    role: Role = Role.DATA


@dataclass(frozen=True)
class Line:
    """One labelled figure in a `Block`.

    Attributes:
        label: What the figure is.
        value: The figure itself, or None for a heading line.
        fmt: How the value reads.
        note: A trailing remark, such as the currency or how much room is left.
    """

    label: str
    value: object = None
    fmt: Fmt = Fmt.TEXT
    note: str = ""


@dataclass(frozen=True)
class Block:
    """A small labelled panel written above a table.

    Attributes:
        title: The panel's heading.
        lines: Its figures, in the order they should read.
    """

    title: str
    lines: tuple[Line, ...] = ()


@dataclass(frozen=True)
class Table:
    """One exported table: a sheet in a workbook, or a whole CSV.

    Attributes:
        name: The sheet name, before it is made legal for Excel.
        columns: The columns, in display order.
        rows: The lines, in display order.
        blocks: Panels written above the table.
        notes: Plain-text lines written under the table, disclosing what it
            could not show and what it converted at.
        tab_color: An accent for the sheet tab, as `RRGGBB`.
        freeze: How many leading columns stay put while the sheet is scrolled
            sideways. Enough of them that a row still says what it is once the
            figures being read have scrolled into view.
    """

    name: str
    columns: tuple[Col, ...]
    rows: tuple[Row, ...] = ()
    blocks: tuple[Block, ...] = ()
    notes: tuple[str, ...] = ()
    tab_color: str | None = None
    freeze: int = 1

    @property
    def headers(self) -> list[str]:
        """Every column heading, in display order."""
        return [column.header for column in self.columns]

    @property
    def data_rows(self) -> list[Row]:
        """The lines that are data rather than totals or spacers."""
        return [row for row in self.rows if row.role in DATA_ROLES]

    @property
    def grouped(self) -> bool:
        """Whether any column asks for a band above the header."""
        return any(column.group for column in self.columns)


DATA_ROLES = frozenset({Role.DATA, Role.MUTED, Role.CLOSED})

# Decimals a reader should see, per role. A workbook displays a value at this
# precision while the cell keeps every digit; a CSV, having nowhere to hide the
# rest, rounds to it.
DECIMALS: dict[Fmt, int] = {
    Fmt.ID: 0,
    Fmt.MONEY: 2,
    Fmt.MONEY_SIGNED: 2,
    Fmt.PRICE: 4,
    Fmt.PRICE_SIGNED: 4,
    Fmt.UNITS: 6,
    Fmt.PERCENT: 2,
    Fmt.PERCENT_SIGNED: 2,
    Fmt.RATE: 4,
}

# The roles whose values are numbers rather than text.
NUMERIC: frozenset[Fmt] = frozenset(DECIMALS)

# The roles rendered as a percentage of a ratio.
PERCENTS: frozenset[Fmt] = frozenset({Fmt.PERCENT, Fmt.PERCENT_SIGNED})

# The roles whose sign carries meaning, and is worth colouring.
SIGNED: frozenset[Fmt] = frozenset(
    {Fmt.MONEY_SIGNED, Fmt.PRICE_SIGNED, Fmt.PERCENT_SIGNED},
)


def row_of(
    columns: Sequence[Col],
    values: Mapping[str, object],
    role: Role = Role.DATA,
) -> Row:
    """Build a row from a heading-keyed mapping.

    A mapping is how a builder reads: it names the cells it fills and lets the
    rest fall blank, which is exactly what a totals line wants.

    Args:
        columns: The table's columns, which set the order.
        values: Cell values keyed by column heading.
        role: What kind of line this is.

    Returns:
        The row, with a blank for every column the mapping omitted.
    """
    return Row(tuple(values.get(column.header) for column in columns), role)


def blank_row(columns: Sequence[Col]) -> Row:
    """Build the spacer that separates the data from the totals."""
    return Row(tuple(None for _ in columns), Role.BLANK)


# Numbers, excluding the booleans Python counts among them.
_NUMBERS = (int, float, Decimal)


def is_blank(value: object) -> bool:
    """Report whether a cell holds nothing, whatever the source called it.

    A frame cell arrives as `NaN` or `pd.NA`, an engine figure as `None`. All
    three mean the same thing to a reader, and all three must stay empty rather
    than print as a zero.

    Args:
        value: The stored value.

    Returns:
        True when there is nothing to show.
    """
    if value is None:
        return True
    try:
        # A cell is `object`, which the stubs' overloads do not cover.
        return bool(pd.isna(value))  # ty: ignore[no-matching-overload]
    except (TypeError, ValueError):  # pragma: no cover - only arrays reach here
        return False


def as_number(value: object) -> float | int | Decimal | None:
    """Read a cell as the number it is, or None when it is not one."""
    if is_blank(value) or isinstance(value, bool) or not isinstance(value, _NUMBERS):
        return None
    return value


def render(fmt: Fmt, value: object, *, quiet: bool = False) -> str:
    """Render one value the way its format displays it.

    The workbook leaves this to Excel and uses it only to size columns. A CSV,
    having no display layer of its own, writes exactly this.

    Args:
        fmt: How the column reads.
        value: The stored value.
        quiet: Leave a zero blank rather than printing it.

    Returns:
        The text a reader would see, or an empty string for a blank.
    """
    number = as_number(value)
    if number is None:
        return "" if is_blank(value) else str(value)
    decimals = DECIMALS.get(fmt)
    if decimals is None:
        return str(value)
    if quiet and not number:
        return ""
    if fmt in PERCENTS:
        return f"{float(number) * 100:,.{decimals}f}%"
    text = f"{float(number):,.{decimals}f}"
    if fmt is Fmt.UNITS and "." in text:
        return text.rstrip("0").rstrip(".")
    return text
