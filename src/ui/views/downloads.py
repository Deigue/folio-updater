"""Rendering what a broker download retrieved."""

from __future__ import annotations

from typing import TYPE_CHECKING

from exporters.sheet_spec import Fmt
from ui.layout.columns import FormattedColumn
from ui.vocabulary import DOWNLOAD_DROP_ORDER
from ui.widgets import show_data_table

if TYPE_CHECKING:
    from services import PreviewTable

PREVIEW_ROWS = 10
DESCRIPTION_WIDTH = 30


def show_preview(preview: PreviewTable) -> None:
    """Show the first few source rows a download retrieved."""
    show_data_table(
        [
            {
                column: _shorten(cell) if column == "Description" else cell
                for column, cell in zip(preview.columns, row, strict=True)
            }
            for row in preview.rows
        ],
        title=f"{preview.title} ({preview.total} total)",
        max_rows=PREVIEW_ROWS,
        drop_order=DOWNLOAD_DROP_ORDER,
        columns=(
            FormattedColumn("Date", fmt=Fmt.DATE, no_wrap=True),
            FormattedColumn("Currency", fmt=Fmt.CURRENCY, no_wrap=True),
        ),
    )


def _shorten(description: str) -> str:
    """Cut a description to the width the preview has room for."""
    if len(description) > DESCRIPTION_WIDTH:
        return description[:DESCRIPTION_WIDTH] + "..."
    return description
