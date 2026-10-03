"""Render `Table` descriptions into a styled Excel workbook.

Two layout rules are deliberate. The heading row is frozen and filtered, so a
sheet can be sorted and filtered like the grid it is; and the totals sit **below a
blank row**, outside the filtered range, so re-sorting never scrambles them into
the data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from openpyxl import Workbook
from openpyxl.formatting.rule import DataBarRule
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.hyperlink import Hyperlink

from exporters import excel_style as style
from exporters.table import (
    DATA_ROLES,
    NUMERIC,
    SIGNED,
    Fmt,
    Link,
    Role,
    Table,
    as_number,
    is_blank,
    render,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping, Sequence
    from pathlib import Path

    from openpyxl.cell.cell import Cell
    from openpyxl.styles import Font
    from openpyxl.worksheet.worksheet import Worksheet

    from exporters.table import Block, Col, Row

# Excel's own limits on what a sheet may be called.
_NAME_LIMIT = 31
_ILLEGAL = str.maketrans(dict.fromkeys(r"[]:*?/\\", "-"))
_FALLBACK_NAME = "Sheet"


def sheet_name(name: str, taken: Collection[str] = ()) -> str:
    """Make a scope's label legal, and unique within one workbook.

    Args:
        name: The label the report would like to use.
        taken: Names already given to other sheets.

    Returns:
        A name Excel accepts, never empty and never a duplicate.
    """
    cleaned = name.translate(_ILLEGAL).strip("'").strip() or _FALLBACK_NAME
    cleaned = cleaned[:_NAME_LIMIT]
    if cleaned not in taken:
        return cleaned

    for suffix in range(2, 100):
        marker = f"~{suffix}"
        candidate = f"{cleaned[: _NAME_LIMIT - len(marker)]}{marker}"
        if candidate not in taken:
            return candidate
    return cleaned[: _NAME_LIMIT - 4] + "~999"  # pragma: no cover - 98 clashes


def write_workbook(path: Path, tables: Sequence[Table]) -> None:
    """Write every table as its own sheet of one workbook.

    Args:
        path: Where the workbook is written.
        tables: The tables, in the order their sheets should appear.
    """
    workbook = Workbook()
    default = workbook.active
    if default is not None:
        workbook.remove(default)

    # Every sheet is named before any is written, so a link can point forward to it
    names: dict[str, str] = {}
    taken: list[str] = []
    for table in tables:
        name = sheet_name(table.name, taken)
        taken.append(name)
        names.setdefault(table.name, name)

    for table, name in zip(tables, taken, strict=True):
        write_sheet(workbook.create_sheet(name), table, names)

    if not taken:  # pragma: no cover - callers always pass at least one table
        workbook.create_sheet(_FALLBACK_NAME)
    _show_a_visible_sheet(workbook)
    workbook.save(path)


def _show_a_visible_sheet(workbook: Workbook) -> None:
    """Open the workbook on its first visible sheet.

    Excel refuses a workbook whose active sheet is hidden, and one with every
    sheet hidden, so the first sheet is shown again if nothing else is.
    """
    sheets = workbook.worksheets
    visible = [
        index for index, sheet in enumerate(sheets) if sheet.sheet_state == "visible"
    ]
    if not visible:
        sheets[0].sheet_state = "visible"
        visible = [0]
    workbook.active = visible[0]
    for index, sheet in enumerate(sheets):
        sheet.sheet_view.tabSelected = index == visible[0]


def write_sheet(
    worksheet: Worksheet,
    table: Table,
    names: Mapping[str, str] | None = None,
) -> None:
    """Lay one table out on one worksheet.

    Args:
        worksheet: The sheet to write into, assumed empty.
        table: What to write.
        names: Each table's name mapped to the sheet it was written to, so a
            link can find its target. A link to anything missing from it is
            written as plain text.
    """
    links = names or {}
    cursor = 1
    if table.nav is not None:
        nav = worksheet.cell(row=cursor, column=1, value=_value(table.nav))
        _link(nav, table.nav, links)
        cursor += 1
    cursor = _write_blocks(worksheet, table.blocks, links, cursor)
    header_row, last_data_row, cursor = _write_grid(worksheet, table, cursor, links)

    for section in table.sections:
        title = worksheet.cell(row=cursor + 1, column=1, value=section.name)
        title.font = style.TITLE_FONT
        _, _, cursor = _write_grid(worksheet, section, cursor + 2, links)

    _finish(worksheet, table, header_row, last_data_row)
    if table.hidden:
        worksheet.sheet_state = "hidden"


def _write_grid(
    worksheet: Worksheet,
    table: Table,
    start: int,
    links: Mapping[str, str],
) -> tuple[int, int, int]:
    """Write one table's header, rows and notes, starting at a given row.

    Args:
        worksheet: The sheet to write into.
        table: The table whose grid is written. Its blocks and sections are
            the caller's business.
        start: The row the grid starts at.
        links: Each table's name mapped to the sheet it was written to.

    Returns:
        The header row, the last row of the filterable data, and the first
        free row after the notes.
    """
    edges = _band_edges(table.columns)
    layout = _Layout(edges=edges, links=links)
    cursor = start
    if table.grouped:
        _write_groups(worksheet, table.columns, cursor, edges)
        cursor += 1
    header_row = cursor
    _write_header(worksheet, table.columns, header_row, edges)

    row_number = header_row
    last_data_row = header_row
    filtered = True
    for row in table.rows:
        row_number += 1
        _write_row(worksheet, table.columns, row, row_number, layout)
        if filtered and row.role in DATA_ROLES:
            last_data_row = row_number
        else:
            # The spacer ends the filtered range, and the totals below it stay
            # out of reach of a re-sort.
            filtered = False

    notes_row = row_number + 2
    _write_notes(worksheet, table.notes, notes_row, links)
    return header_row, last_data_row, notes_row + len(table.notes)


def _write_blocks(
    worksheet: Worksheet,
    blocks: Iterable[Block],
    links: Mapping[str, str],
    start: int = 1,
) -> int:
    """Write the labelled panels above the table, returning the next free row."""
    cursor = start
    for block in blocks:
        title = worksheet.cell(row=cursor, column=1, value=block.title)
        title.font = style.TITLE_FONT
        cursor += 1
        for line in block.lines:
            label = worksheet.cell(row=cursor, column=1, value=_value(line.label))
            label.font = style.LABEL_FONT
            _link(label, line.label, links)
            cell = worksheet.cell(row=cursor, column=2, value=_value(line.value))
            cell.number_format = _number_format(line.fmt, line.value)
            if line.note:
                note = worksheet.cell(row=cursor, column=3, value=line.note)
                note.font = style.MUTED_FONT
            cursor += 1
        cursor += 1
    return cursor


@dataclass(frozen=True)
class _Layout:
    """What every row of one sheet is drawn against.

    Attributes:
        edges: The one-based columns a band starts at, which carry a border.
        links: Each table's name mapped to the sheet it was written to.
    """

    edges: frozenset[int]
    links: Mapping[str, str]


def _band_edges(columns: Sequence[Col]) -> frozenset[int]:
    """Find the columns a band starts at, as one-based indices.

    The first column is never an edge: a border on the left of the sheet marks
    nothing.
    """
    return frozenset(
        index
        for index, column in enumerate(columns, start=1)
        if index > 1 and column.group != columns[index - 2].group
    )


def _write_groups(
    worksheet: Worksheet,
    columns: Sequence[Col],
    row: int,
    edges: frozenset[int],
) -> None:
    """Draw the merged band naming each family of columns."""
    start = 0
    while start < len(columns):
        group = columns[start].group
        end = start
        while end + 1 < len(columns) and columns[end + 1].group == group:
            end += 1
        if group:
            cell = worksheet.cell(row=row, column=start + 1, value=group)
            cell.font = style.GROUP_FONT
            cell.alignment = style.GROUP_ALIGNMENT
            cell.fill = style.ROW_FILLS[Role.SUBTOTAL]
            cell.border = style.edged(left=start + 1 in edges, top=False)
            if end > start:
                worksheet.merge_cells(
                    start_row=row,
                    start_column=start + 1,
                    end_row=row,
                    end_column=end + 1,
                )
        start = end + 1


def _write_header(
    worksheet: Worksheet,
    columns: Sequence[Col],
    row: int,
    edges: frozenset[int],
) -> None:
    """Write the heading row, which is what the filter and freeze key on."""
    for index, column in enumerate(columns, start=1):
        cell = worksheet.cell(row=row, column=index, value=column.header)
        cell.font = style.HEADER_FONT
        cell.fill = style.HEADER_PATTERN
        cell.alignment = style.HEADER_ALIGNMENT
        cell.border = style.edged(left=index in edges, top=False)


def _write_row(
    worksheet: Worksheet,
    columns: Sequence[Col],
    row: Row,
    number: int,
    layout: _Layout,
) -> None:
    """Write one line, styling each cell by its column's role and the row's."""
    edges = layout.edges
    fill = style.ROW_FILLS.get(row.role)
    font = style.ROW_FONTS.get(row.role)
    bold = row.role in style.BOLD_ROLES
    cells = enumerate(zip(columns, row.cells, strict=True), start=1)
    for index, (column, raw) in cells:
        cell = worksheet.cell(row=number, column=index, value=_value(raw))
        cell.number_format = _number_format(column.fmt, raw, quiet=column.quiet)
        if fill is not None:
            cell.fill = fill
        if index in edges or row.role is Role.TOTAL:
            cell.border = style.edged(left=index in edges, top=row.role is Role.TOTAL)
        signed = _sign_font(column.fmt, raw, bold=bold)
        if signed is not None:
            cell.font = signed
        elif font is not None:
            cell.font = font
        _link(cell, raw, layout.links)


def _write_notes(
    worksheet: Worksheet,
    notes: Sequence[str | Link],
    row: int,
    links: Mapping[str, str],
) -> None:
    """Write the disclosure lines under the table."""
    for offset, note in enumerate(notes):
        cell = worksheet.cell(row=row + offset, column=1, value=_value(note))
        cell.font = style.NOTE_FONT
        _link(cell, note, links)


def _link(cell: Cell, raw: object, links: Mapping[str, str]) -> None:
    """Point a cell at the sheet its link names, when the workbook holds it."""
    if not isinstance(raw, Link):
        return
    target = links.get(raw.sheet)
    if target is None:
        return
    cell.hyperlink = Hyperlink(ref=cell.coordinate, location=f"'{target}'!A1")
    cell.font = style.LINK_FONT


def _finish(
    worksheet: Worksheet,
    table: Table,
    header_row: int,
    last_data_row: int,
) -> None:
    """Freeze, filter, size and accent the finished sheet."""
    last_column = get_column_letter(len(table.columns))
    frozen = min(max(table.freeze, 0), len(table.columns))
    pinned = header_row + 1 if table.freeze_rows else 1
    worksheet.freeze_panes = f"{get_column_letter(frozen + 1)}{pinned}"
    if last_data_row > header_row:
        worksheet.auto_filter.ref = f"A{header_row}:{last_column}{last_data_row}"

    # Every table on the sheet shares its columns, so each is sized to the widest
    # thing any of them, or the panels above, puts there.
    widths = _block_widths(table.blocks)
    for grid in (table, *table.sections):
        for index, column in enumerate(grid.columns):
            needed = _width(index, column, grid.rows)
            widths[index] = max(widths.get(index, 0), needed)
    for index, width in widths.items():
        letter = get_column_letter(index + 1)
        worksheet.column_dimensions[letter].width = min(width, style.MAX_WIDTH)

    if table.tab_color:
        worksheet.sheet_properties.tabColor = table.tab_color

    _write_bars(worksheet, table, header_row, last_data_row)


def _write_bars(
    worksheet: Worksheet,
    table: Table,
    header_row: int,
    last_data_row: int,
) -> None:
    """Draw an in-cell bar behind the columns that asked for one."""
    if last_data_row <= header_row:
        return
    rule = DataBarRule(
        start_type="num",
        start_value=0,
        end_type="num",
        end_value=style.BAR_FULL_AT / 100,
        color=style.BAR_COLOR,
        showValue=True,
    )
    for index, column in enumerate(table.columns, start=1):
        if not column.bar:
            continue
        letter = get_column_letter(index)
        span = f"{letter}{header_row + 1}:{letter}{last_data_row}"
        worksheet.conditional_formatting.add(span, rule)


def _value(raw: object) -> object:
    """Read one cell value, turning every flavour of blank into a real one.

    A number is written as the number it is, at full precision: the format
    decides what a reader sees, never what the cell holds.
    """
    if isinstance(raw, Link):
        return raw.text
    if is_blank(raw):
        return None
    number = as_number(raw)
    return str(raw) if number is None else number


def _number_format(fmt: Fmt, raw: object, *, quiet: bool = False) -> str:
    """Pick the format one cell displays at."""
    if fmt is Fmt.UNITS and _whole(raw):
        chosen = style.WHOLE_UNITS
    else:
        chosen = style.NUMBER_FORMATS[fmt]
    return style.quiet(chosen) if quiet and fmt in NUMERIC else chosen


def _whole(raw: object) -> bool:
    """Report whether a share count has no fraction worth showing."""
    number = as_number(raw)
    return number is not None and float(number) == int(number)


def _sign_font(fmt: Fmt, raw: object, *, bold: bool) -> Font | None:
    """Colour a figure whose sign carries meaning."""
    number = as_number(raw) if fmt in SIGNED else None
    return None if number is None else style.sign_font(float(number), bold=bold)


def _block_widths(blocks: Iterable[Block]) -> dict[int, int]:
    """Measure what the panels above a table need of its first two columns.

    A note is left out: its neighbours on a panel row are empty, so it spills
    into them the way any long text does, instead of stretching a column the
    table below has to live with.
    """
    widths: dict[int, int] = {}
    for block in blocks:
        for line in block.lines:
            cells = (render(Fmt.TEXT, line.label), render(line.fmt, line.value))
            for index, text in enumerate(cells):
                needed = len(text) + style.WIDTH_PADDING
                widths[index] = max(widths.get(index, 0), needed)
    return widths


def _width(index: int, column: Col, rows: Sequence[Row]) -> int:
    """Size a column from the widest thing it has to show.

    The heading is measured with room for its filter button, which Excel draws
    inside the cell and which would otherwise sit on top of the text.
    """
    if column.width is not None:
        return column.width
    longest = len(column.header) + style.FILTER_PADDING
    for row in rows:
        if index < len(row.cells):
            rendered = render(column.fmt, row.cells[index], quiet=column.quiet)
            longest = max(longest, len(rendered) + style.WIDTH_PADDING)
    widest = style.NUMBER_MAX_WIDTH if column.fmt in NUMERIC else style.MAX_WIDTH
    return min(max(longest, style.MIN_WIDTH), widest)
