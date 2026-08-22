"""Terminal related functions."""

from __future__ import annotations

import os

from ui.console import active_console

# Lines used by the stats summary panel at the top.
STATS_PANEL_LINES = 3

# Lines used by the expansion prompt at the bottom.
PROMPT_LINES = 2

# Chrome for a bordered, headered table: top border, header text, header
# separator, and bottom border (verified against Rich's actual output).
TABLE_HEADER_HEIGHT = 4

# Page progress display height (e.g., "Showing transactions 1-15 of 100").
PAGE_PROGRESS_HEIGHT = 1

# The console.rule() line printed above each page (title + page counter).
PAGE_RULE_HEIGHT = 1

# Slack kept below the terminal's last row. Without it, a page's last line
# lands exactly on the final row; many terminals (incl. VS Code's) then
# scroll by one on the next newline, clipping the top of the page - header
# included - out of view.
SAFETY_MARGIN = 1

# The fewest content lines a page is ever given, however cramped the terminal.
MINIMUM_HEIGHT = 10


def terminal_size() -> tuple[int, int]:
    """Get terminal dimensions (width, height).

    Returns:
        Tuple of (width, height) in characters
    """
    try:
        term_size = active_console().size
    except (AttributeError, OSError):  # pragma: no cover
        return 80, 24  # Sensible fallback defaults
    else:
        return term_size.width, term_size.height


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
        # Paged full-screen table: console.rule + progress line + prompt.
        # No stats panel is shown in this flow.
        reserved_lines = PAGE_RULE_HEIGHT + PAGE_PROGRESS_HEIGHT + PROMPT_LINES
    else:
        # Tiled audit-block view: stats panel + expansion prompt.
        reserved_lines = STATS_PANEL_LINES + PROMPT_LINES
    reserved_lines += TABLE_HEADER_HEIGHT if table else 0
    reserved_lines += SAFETY_MARGIN + reserved
    return max(height - reserved_lines, MINIMUM_HEIGHT)


def is_test_environment() -> bool:
    """Check if code is running in a test environment.

    Returns:
        True if running under pytest, False otherwise.
    """
    return "PYTEST_CURRENT_TEST" in os.environ
