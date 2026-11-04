"""Render a `Sheet` description as flat CSV."""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING

from exporters.sheet_spec import Fmt, render

if TYPE_CHECKING:
    from pathlib import Path

    from exporters.sheet_spec import Sheet


def write_csv(path: Path, sheet: Sheet) -> None:
    """Write one sheet as a CSV file: its lists, then its only table.

    Lists and notes are written around the grid, exactly where the workbook
    puts them, so the two formats say the same things in the same order.

    Args:
        path: Where the file is written.
        sheet: What to write.
    """
    table = sheet.main_table
    width = len(table.columns)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for sheet_list in sheet.lists:
            writer.writerow(_padded([sheet_list.title], width))
            for line in sheet_list.lines:
                label = render(Fmt.TEXT, line.label)
                cells = [label, render(line.fmt, line.value), line.note]
                writer.writerow(_padded(cells, width))
            writer.writerow(_padded([], width))

        writer.writerow(table.headers)
        for row in table.rows:
            writer.writerow(
                [
                    render(column.fmt, value, quiet=column.quiet)
                    for column, value in zip(table.columns, row.cells, strict=True)
                ],
            )

        if table.notes:
            writer.writerow(_padded([], width))
            for note in table.notes:
                writer.writerow(_padded([render(Fmt.TEXT, note)], width))


def _padded(cells: list[str], width: int) -> list[str]:
    """Pad a short line out to the table's width, so every record matches."""
    return [*cells, *[""] * max(width - len(cells), 0)]
