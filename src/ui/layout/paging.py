"""The next/previous/quit pager for CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from term.console import active_console, console_print
from term.keys import getch
from term.size import terminal_size
from ui.vocabulary import TABLE_BORDER

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

# Lines used by the stats summary panel at the top.
STATS_PANEL_LINES = 3

# Lines used by the expansion prompt at the bottom.
PROMPT_LINES = 2

# The pager's one bottom line: rows shown and the keys that move between pages.
NAVIGATION_HEIGHT = 1

# Chrome for a bordered, headered table: top border, header text, header
# separator, and bottom border (verified against Rich's actual output).
TABLE_HEADER_HEIGHT = 4

# The console.rule() line printed above each page (title + page counter).
PAGE_RULE_HEIGHT = 1

# Slack kept below the terminal's last row. Without it, a page's last line
# lands exactly on the final row; many terminals (VS Code's included) then
# scroll by one on the next newline, which clips the top of the page, header
# and all, out of view.
SAFETY_MARGIN = 1

# The fewest content lines a page is ever given, however cramped the terminal.
MINIMUM_HEIGHT = 10


def available_height(
    *,
    table: bool = False,
    pages: bool = False,
    reserved: int = 0,
) -> int:
    """Calculate available height for content after reserved UI elements.

    Args:
        table: Whether a bordered, headered table is being drawn.
        pages: Whether this is the paged full-screen flow.
        reserved: Additional lines to reserve, cache badge or header stacking

    Returns:
        The number of content lines that fit.
    """
    _, height = terminal_size()
    if pages:
        # Paged full-screen table: console.rule + navigation line.
        # No stats panel is shown in this flow.
        reserved_lines = PAGE_RULE_HEIGHT + NAVIGATION_HEIGHT
    else:
        # Tiled audit view: stats panel + expansion prompt.
        reserved_lines = STATS_PANEL_LINES + PROMPT_LINES
    reserved_lines += TABLE_HEADER_HEIGHT if table else 0
    reserved_lines += SAFETY_MARGIN + reserved
    return max(height - reserved_lines, MINIMUM_HEIGHT)


def _navigation_line(start: int, end: int, total: int, page: int, pages: int) -> str:
    """Render the rows a page shows and the keys that move off it, on one line."""
    keys = []
    if page < pages - 1:
        keys.append("[bold]n[/bold] next")
    if page > 0:
        keys.append("[bold]p[/bold] prev")
    keys.append("[bold]q[/bold] quit")
    return f"[dim]{start + 1}-{end} of {total} · {' · '.join(keys)}[/dim]"


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
    page_footer: Sequence[str] = (),
    final_footer: Sequence[str] = (),
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
        page_footer: Lines printed under every page, one terminal line each.
        final_footer: Lines printed once the rows are all shown or the pager
            exits: pages draw on a screen discarded on quit, so this copy is the
            one left behind.
    """
    if total_rows == 0:
        console_print(f"[yellow]No transactions to display for {title}[/yellow]")
        return

    # Printed once here for the single-page and unpaged paths below.
    if badge:
        console_print(badge)

    # Nobody can answer the n/p/q prompt when output is piped or redirected
    # (a script, CI, `> file`), and getch would wait on the console forever.
    if not active_console().is_interactive:
        render(0, total_rows)
        _print_lines(final_footer)
        return

    if page_size is None:
        page_size = available_height(
            table=True,
            pages=True,
            # The badge is redrawn above every page, so it costs the table a row.
            reserved=reserved + (1 if badge else 0) + len(page_footer),
        )

    if total_rows <= page_size:
        render(0, total_rows)
        _print_lines(final_footer)
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
                page_footer,
            )
            if should_exit:
                break

    active_console().rule("End of Transactions", style="dim")
    _print_lines(final_footer)


def _print_lines(lines: Sequence[str]) -> None:
    """Print each line as it is."""
    for line in lines:
        console_print(line)


def page_title(title: str, start: int, end: int, total: int) -> str | None:
    """Give the title to a table that is not paged, and none to a page of a longer one.

    The pager's rule already names a page, so only a table shown whole carries
    the title itself.

    Args:
        title: The display's title.
        start: First row of the page being drawn.
        end: One past its last row.
        total: Rows in the whole display.

    Returns:
        The title when the page holds every row, otherwise None.
    """
    return title if start == 0 and end == total else None


def _render_one_page(  # noqa: PLR0917
    current_page: int,
    total_pages: int,
    total_rows: int,
    page_size: int,
    title: str,
    badge: str | None,
    render: Callable[[int, int], None],
    footer: Sequence[str],
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
        style=TABLE_BORDER,
    )
    if badge:
        console_print(badge)

    render(start_idx, end_idx)
    _print_lines(footer)
    active_console().print(
        _navigation_line(start_idx, end_idx, total_rows, current_page, total_pages),
    )

    try:
        user_input = getch()
        return _handle_pagination_input(user_input, current_page, total_pages)
    except (KeyboardInterrupt, EOFError):
        return current_page, True
