"""What an exported sheet holds, independent of the file it lands in."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from domain import (
    COMPACT_RANGES,
    DECIMALS,
    MAGNITUDES,
    PERCENTS,
    UNGROUPED,
    Column,
    Fmt,
    Measure,
)
from domain.cells import is_blank

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence


class Role(StrEnum):
    """What kind of line a row is, which decides how it is drawn."""

    DATA = "data"
    MUTED = "muted"  # reference data rather than performance
    CLOSED = "closed"  # a holding that is no longer held
    BLANK = "blank"  # a spacer, and the end of the filtered range
    SUBTOTAL = "subtotal"
    TOTAL = "total"


def _unread(_source: object) -> None:
    """Read nothing, for a column whose rows are built from a mapping instead."""


@dataclass(frozen=True)
class Col[R]:
    """One column of a table, for rows read from an `R`.

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
        key: A short lowercase name to pick the column by, for a column listed
            in a catalog.
        label: The figure's name spelled out, for a list with room for it,
            where the header is abbreviated to fit a column.
        read: Reads the column's value from one `R`, for a column whose rows
            are built from it rather than from a heading-keyed mapping, which
            leaves it reading nothing.
        coarse: A percentage that can spare its second decimal on a narrow
            terminal.
        measure: What kind of figure it holds, for shading it in a terminal.
        shaded_by: Reads the ratio that decides the shade, for a figure shaded
            by another one (a money gain by its percentage) rather than itself.
    """

    header: str
    fmt: Fmt = Fmt.TEXT
    group: str | None = None
    width: int | None = None
    bar: bool = False
    quiet: bool = False
    key: str = ""
    label: str = ""
    read: Callable[[R], object] = _unread
    coarse: bool = False
    measure: Measure | None = None
    shaded_by: Callable[[R], Decimal | None] | None = None

    @property
    def full_label(self) -> str:
        """The figure's name spelled out, or its header when that is already whole."""
        return self.label or self.header


@dataclass(frozen=True)
class Row:
    """One row of a table, with one cell per column.

    Attributes:
        cells: Values in column order. `None` is a genuine blank.
        role: What kind of line this is.
    """

    cells: tuple[object, ...]
    role: Role = Role.DATA


@dataclass(frozen=True)
class Link:
    """A cell that takes the reader to another sheet of the same workbook.

    Attributes:
        text: What the cell reads.
        sheet: The name of the sheet it points at.
    """

    text: str
    sheet: str


@dataclass(frozen=True)
class Line:
    """One labelled figure in a `SheetList`.

    Attributes:
        label: What the figure is.
        value: The figure itself, or None for a heading line.
        fmt: How the value reads.
        note: A trailing remark, such as the currency or how much room is left.
    """

    label: str | Link
    value: object = None
    fmt: Fmt = Fmt.TEXT
    note: str = ""


@dataclass(frozen=True)
class SheetList:
    """A titled list of labelled figures, written above a sheet's tables.

    Attributes:
        title: The list's heading.
        lines: Its figures, in the order they should read.
    """

    title: str
    lines: tuple[Line, ...] = ()


@dataclass(frozen=True)
class SheetTable:
    """One table on a sheet: its columns, its rows and what it discloses.

    Attributes:
        columns: The columns, in display order.
        rows: The rows, in display order.
        notes: Plain-text lines written under the table, disclosing what it
            could not show and what it converted at.
        title: The heading written above the table, for any but a sheet's first.
    """

    columns: tuple[Col, ...]
    rows: tuple[Row, ...] = ()
    notes: tuple[str | Link, ...] = ()
    title: str = ""

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


@dataclass(frozen=True)
class Sheet:
    """One sheet of a workbook, or a whole CSV.

    Attributes:
        name: The sheet name, before it is made legal for Excel.
        tables: Its tables, top to bottom, at least one. Only the first is
            filtered and frozen, and a CSV holds exactly one.
        lists: Titled lists written above the tables.
        tab_color: An accent for the sheet tab, as `RRGGBB`.
        freeze: How many leading columns stay put while the sheet is scrolled
            sideways. Enough of them that a row still says what it is once the
            figures being read have scrolled into view.
        freeze_rows: Keep everything above the first table's header in view while
            the sheet scrolls. Off for a sheet whose lists above are too tall to
            pin, which would leave little of the screen to scroll.
        hidden: Keep the sheet out of the tab strip. Still accessible via right-click.
        nav: A link written in the sheet's first row, above everything else.
    """

    name: str
    tables: tuple[SheetTable, ...]
    lists: tuple[SheetList, ...] = ()
    tab_color: str | None = None
    freeze: int = 1
    freeze_rows: bool = True
    hidden: bool = False
    nav: Link | None = None

    @property
    def main_table(self) -> SheetTable:
        """The first table: the one filtered and frozen, and all a CSV holds."""
        return self.tables[0]


DATA_ROLES = frozenset({Role.DATA, Role.MUTED, Role.CLOSED})

# How a stored transaction column reads, in a sheet or a terminal table.
# Anything unlisted, including a user's optional columns, is text.
TXN_FORMATS: dict[str, Fmt] = {
    str(Column.Txn.TXN_DATE): Fmt.DATE,
    str(Column.Txn.SETTLE_DATE): Fmt.DATE,
    str(Column.Txn.ACTION): Fmt.ACTION,
    str(Column.Txn.AMOUNT): Fmt.MONEY_SIGNED,
    str(Column.Txn.CURRENCY): Fmt.CURRENCY,
    str(Column.Txn.ACCOUNT): Fmt.ACCOUNT,
    str(Column.Txn.PRICE): Fmt.PRICE,
    str(Column.Txn.UNITS): Fmt.UNITS,
    str(Column.Txn.FEE): Fmt.MONEY,
    str(Column.Txn.SETTLE_CALCULATED): Fmt.ID,
}


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


def read_cells[R](columns: Sequence[Col[R]], source: R) -> dict[str, object]:
    """Read each column's value from one source, keyed by heading for `row_of`."""
    return {column.header: column.read(source) for column in columns}


def blank_row(columns: Sequence[Col]) -> Row:
    """Build the spacer that separates the data from the totals."""
    return Row(tuple(None for _ in columns), Role.BLANK)


# Numbers, excluding the booleans Python counts among them.
_NUMBERS = (int, float, Decimal)


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
    if isinstance(value, Link):
        value = value.text
    number = as_number(value)
    decimals = DECIMALS.get(fmt)
    if number is None or decimals is None:
        return "" if is_blank(value) else str(value)
    if quiet and not number:
        return ""
    return _number(fmt, float(number), decimals)


def _number(fmt: Fmt, number: float, decimals: int) -> str:
    """Render a number at its format's precision, in its format's shape."""
    if fmt in COMPACT_RANGES:
        return _compact(number, COMPACT_RANGES[fmt], decimals)
    if fmt in PERCENTS:
        return f"{number * 100:,.{decimals}f}%"
    grouping = "" if fmt in UNGROUPED else ","
    text = f"{number:{grouping}.{decimals}f}"
    if fmt is Fmt.UNITS and "." in text:
        return text.rstrip("0").rstrip(".")
    return text


def _compact(number: float, sizes: tuple[int, int], decimals: int) -> str:
    """Abbreviate a figure by the largest size in range it reaches: 3.81T, 27.01M."""
    floor, ceiling = sizes
    size, suffix = next(
        (size, suffix)
        for size, suffix in (*MAGNITUDES, (1, ""))
        if size <= ceiling and (abs(number) >= size or size <= floor)
    )
    return f"{number / size:,.{decimals}f}{suffix}"
