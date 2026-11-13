"""Rendering what a broker download retrieved."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from exporters.sheet_spec import Fmt
from term import console_success, console_warning
from ui.layout.columns import FormattedColumn
from ui.vocabulary import DOWNLOAD_DROP_ORDER
from ui.widgets import show_data_table

if TYPE_CHECKING:
    from collections.abc import Sequence

    from services import DownloadResult, PreviewTable

# The widest a preview cell grows; a longer value is cut with an ellipsis.
CELL_WIDTH = 15
# How much of a header a column keeps room for, even when its values are shorter.
HEADER_WIDTH = 8


def show_download(result: DownloadResult) -> None:
    """Show each preview, then every file saved, or that nothing came."""
    for preview in result.previews:
        show_preview(preview)
    for path in result.files:
        console_success(f'File saved: "{path}"')
    if not result.files:
        console_warning("No source files downloaded")


def show_preview(preview: PreviewTable) -> None:
    """Show the first source rows of a download, as the broker sent them.

    A column empty in every row shown is left out, and each column is only as
    wide as its longest value shown, up to `CELL_WIDTH`.
    """
    shown = [
        index
        for index in range(len(preview.columns))
        if any(row[index] for row in preview.rows)
    ]
    if not shown:
        return
    columns = [
        _column(preview.columns[index], [row[index] for row in preview.rows])
        for index in shown
    ]
    show_data_table(
        [
            {preview.columns[index]: row[index] for index in shown}
            for row in preview.rows
        ],
        title=f"{preview.title}: showing {len(preview.rows)} of {preview.total}",
        max_rows=len(preview.rows),
        drop_order=DOWNLOAD_DROP_ORDER,
        columns=columns,
    )


def _column(header: str, cells: Sequence[str]) -> FormattedColumn:
    """Size a column to its cells, a date column as dates, numbers right-aligned."""
    filled = [cell for cell in cells if cell]
    longest = max(len(cell) for cell in [*filled, header[:HEADER_WIDTH]])
    return FormattedColumn(
        header,
        fmt=Fmt.DATE if all(_is_date(cell) for cell in filled) else Fmt.TEXT,
        max_width=min(CELL_WIDTH, longest),
        overflow="ellipsis",
        no_wrap=True,
        justify="right" if all(_is_number(cell) for cell in filled) else "left",
    )


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _is_number(text: str) -> bool:
    try:
        Decimal(text)
    except InvalidOperation:
        return False
    return True
