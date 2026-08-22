"""The next/previous/quit pager for CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ui.console import active_console, console_print
from ui.keys import getch
from ui.layout.terminal import available_height, is_test_environment

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable


def _get_pagination_prompt(current_page: int, total_pages: int) -> str:
    """Get navigation prompt based on current page position.

    Args:
        current_page: Current page number (0-indexed)
        total_pages: Total number of pages

    Returns:
        Navigation prompt text
    """
    is_first = current_page == 0
    is_last = current_page == total_pages - 1

    if is_first and not is_last:
        return "[dim]Press [bold]n[/bold] for next page, [bold]q[/bold] to quit[/dim]"
    if is_last and not is_first:
        return (
            "[dim]Press [bold]p[/bold] for previous page, [bold]q[/bold] to quit[/dim]"
        )
    return (
        "[dim]Press [bold]n[/bold] for next,"
        " [bold]p[/bold] for previous, [bold]q[/bold] to quit[/dim]"
    )


def _handle_pagination_input(
    user_input: str,
    current_page: int,
    total_pages: int,
) -> tuple[int, bool]:
    """Handle user input for pagination.

    Args:
        user_input: User's input string
        current_page: Current page number
        total_pages: Total number of pages

    Returns:
        Tuple of (new_page_number, should_exit)
    """
    if user_input == "q":
        return current_page, True
    if user_input == "n" and current_page < total_pages - 1:
        return current_page + 1, False
    if user_input == "p" and current_page > 0:
        return current_page - 1, False
    return current_page, False


def page_frame(
    total_rows: int,
    title: str,
    page_size: int | None,
    render: Callable[[int, int], None],
    *,
    badge: str | None = None,
    reserved: int = 0,
) -> None:
    """Drive the n/p/q pager over a row range, delegating pages to `render`.

    Args:
        total_rows: Number of rows to page through
        title: Title for the display
        page_size: Rows per page. If None, calculated from console height
        render: Called with (start, end) to draw one page of rows
        badge: Optional status line (e.g. from `freshness_badge`) printed
            above every page
        reserved: Further lines each page draws that the height budget cannot see
    """
    if total_rows == 0:
        console_print(f"[yellow]No transactions to display for {title}[/yellow]")
        return

    # Printed once here for the single-page and test-environment paths below.
    if badge:
        console_print(badge)

    if is_test_environment():
        render(0, total_rows)
        return

    if page_size is None:
        page_size = available_height(
            table=True,
            pages=True,
            # The badge is redrawn above every page, so it costs the table a row.
            reserved=reserved + (1 if badge else 0),
        )

    if total_rows <= page_size:
        render(0, total_rows)
        return

    total_pages = (total_rows + page_size - 1) // page_size
    current_page = 0

    # Alternate screen: each page redraws in a scratch buffer the terminal
    # discards on exit.
    with active_console().screen():
        while True:
            current_page, should_exit = _render_one_page(
                current_page,
                total_pages,
                total_rows,
                page_size,
                title,
                badge,
                render,
            )
            if should_exit:
                break

    active_console().rule("End of Transactions", style="dim")


def _render_one_page(  # noqa: PLR0917
    current_page: int,
    total_pages: int,
    total_rows: int,
    page_size: int,
    title: str,
    badge: str | None,
    render: Callable[[int, int], None],
) -> tuple[int, bool]:
    """Draw one page inside the pager loop and read the next navigation input.

    Returns:
        The page to draw next, and whether the pager should exit.
    """
    start_idx = current_page * page_size
    end_idx = min(start_idx + page_size, total_rows)

    active_console().clear()
    active_console().rule(
        f"{title} - Page {current_page + 1}/{total_pages}",
        style="bright_blue",
    )
    if badge:
        console_print(badge)

    render(start_idx, end_idx)

    active_console().print(
        f"[dim]Showing transactions {start_idx + 1}-{end_idx} of {total_rows}[/dim]",
    )

    if current_page == 0 and total_pages == 1:
        return current_page, True  # Only one page, no navigation needed

    prompt = _get_pagination_prompt(current_page, total_pages)
    active_console().print(f"\n{prompt}")

    try:
        user_input = getch()
        return _handle_pagination_input(user_input, current_page, total_pages)
    except (KeyboardInterrupt, EOFError):
        return current_page, True
