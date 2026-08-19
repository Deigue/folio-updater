"""Common UI elements and widgets."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.table import Table

from ui.console import console_panel, console_print
from ui.format import safe_str
from ui.layout.fit import fit_table

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Sequence


def show_data_table(
    data: list[dict[str, Any]],
    title: str | None = None,
    max_rows: int = 50,
    *,
    theme: str = "bright_blue",
    drop_order: Sequence[str] = (),
) -> None:
    """Display generic data in a Rich table.

    Args:
        data: List of dictionaries containing data to display
        title: Optional title for the table
        max_rows: Maximum number of rows to display
        theme: Border color theme for the table
        drop_order: Headers to drop as last resort by least->most important
    """
    if not data:
        console_print("[yellow]No data to display[/yellow]")
        return

    # Limit rows for readability
    display_data = data[:max_rows]
    truncated = len(data) > max_rows

    table = Table(
        title=title,
        show_header=True,
        header_style="bold bright_white",
        border_style=theme,
        expand=False,
    )

    # Add columns based on first row keys
    if display_data:
        for key in display_data[0]:
            table.add_column(str(key), no_wrap=True)

        # Add rows
        for row in display_data:
            table.add_row(*[safe_str(value) for value in row.values()])

    console_print(fit_table(table, drop_order))

    if truncated:
        console_print(
            f"\n[dim]... showing first {max_rows} of {len(data)} items[/dim]",
        )


def show_stats_panel(stats: dict[str, int | str]) -> None:
    """Display statistics in a Rich panel.

    Args:
        stats: Dictionary of statistics to display
    """
    stats_text = "\n".join(
        f"[bold]{key}:[/bold] [bright_white]{value}[/bright_white]"
        for key, value in stats.items()
    )
    console_panel(stats_text, title="Stats", style="bright_blue", expand=False)
