"""Render `Table` descriptions as flat CSV."""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING

from exporters.table import render

if TYPE_CHECKING:
    from pathlib import Path

    from exporters.table import Table


def write_csv(path: Path, table: Table) -> None:
    """Write one table as a CSV file.

    Blocks and notes are written around the grid, exactly where the workbook
    puts them, so the two formats say the same things in the same order.

    Args:
        path: Where the file is written.
        table: What to write.
    """
    width = len(table.columns)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for block in table.blocks:
            writer.writerow(_padded([block.title], width))
            for line in block.lines:
                cells = [line.label, render(line.fmt, line.value), line.note]
                writer.writerow(_padded(cells, width))
            writer.writerow(_padded([], width))

        writer.writerow(table.headers)
        for row in table.rows:
            writer.writerow(
                [
                    render(column.fmt, value)
                    for column, value in zip(table.columns, row.cells, strict=True)
                ],
            )

        if table.notes:
            writer.writerow(_padded([], width))
            for note in table.notes:
                writer.writerow(_padded([note], width))


def _padded(cells: list[str], width: int) -> list[str]:
    """Pad a short line out to the table's width, so every record matches."""
    return [*cells, *[""] * max(width - len(cells), 0)]
