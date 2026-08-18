"""Rich display utilities for CLI commands.

This module provides custom display functions for the folio CLI.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pandas as pd
from rich.columns import Columns
from rich.console import Console, Group, RenderableType
from rich.measure import Measurement
from rich.padding import Padding
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from cli import console as console_module
from cli.console import (
    console,
    console_panel,
    console_print,
    get_symbol,
    progress_console_context,
    supports_unicode,
)
from utils import TXN_ESSENTIALS, Action, Column, TransactionContext

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Container, Generator, Sequence

    from rich.console import JustifyMethod

    from models import ImportResults

# Minimum columns always shown for exclusions
EXCLUSION_BASE_COLUMNS = [Column.Txn.TXN_DATE, Column.Txn.ACTION, Column.Txn.AMOUNT]


@dataclass(frozen=True)
class _ColumnSpec:
    """How a standard transaction column is rendered in a table."""

    max_width: int
    justify: JustifyMethod = "left"
    style: str | None = None


# Standard transaction columns, in display order. Column definitions and row
# cells are both derived from this, so the two can never drift apart.
_TXN_COLUMN_SPECS: dict[str, _ColumnSpec] = {
    Column.Txn.TXN_ID: _ColumnSpec(max_width=6, style="dim"),
    Column.Txn.SETTLE_DATE: _ColumnSpec(max_width=10),
    Column.Txn.TXN_DATE: _ColumnSpec(max_width=10),
    Column.Txn.ACTION: _ColumnSpec(max_width=12),
    Column.Txn.AMOUNT: _ColumnSpec(max_width=12, justify="right"),
    Column.Txn.CURRENCY: _ColumnSpec(max_width=4),
    Column.Txn.PRICE: _ColumnSpec(max_width=10, justify="right"),
    Column.Txn.UNITS: _ColumnSpec(max_width=10, justify="right"),
    Column.Txn.TICKER: _ColumnSpec(max_width=12),
    Column.Txn.ACCOUNT: _ColumnSpec(max_width=15),
    Column.Txn.FEE: _ColumnSpec(max_width=8, justify="right"),
}

# Shown only outside the import context, where TxnIds do not exist yet.
_ID_COLUMNS = (Column.Txn.TXN_ID, Column.Txn.SETTLE_DATE)

# Room an inline "old -> new" diff needs beyond the column's normal width.
_DIFF_EXTRA_WIDTH = 6

# A width no table will reach, used to ask Rich how wide one wants to be.
_UNBOUNDED_WIDTH = 10_000


def _ordered_columns(
    display_df: pd.DataFrame,
    context: TransactionContext,
) -> list[str]:
    """List the columns a transaction table shows, in display order.

    Args:
        display_df: DataFrame being displayed
        context: Context to determine which columns to show

    Returns:
        Standard columns for the context, followed by any non-standard
        columns present in the DataFrame (GENERAL context only).
    """
    columns = [
        column
        for column in _TXN_COLUMN_SPECS
        if context != TransactionContext.IMPORT or column not in _ID_COLUMNS
    ]
    if context != TransactionContext.GENERAL:
        return columns

    known = {*_TXN_COLUMN_SPECS, Column.Txn.SETTLE_CALCULATED}
    columns.extend(str(col) for col in display_df.columns if col not in known)
    return columns


# Cross-platform single character input
try:
    import msvcrt  # Windows

    def _getch() -> str:
        r"""Get a single character from stdin without pressing Enter.

        Special keys (arrows, function keys, etc.) are reported by msvcrt as
        a two-byte sequence: a prefix byte (b"\\x00" or b"\\xe0") followed by
        a scan code byte, neither of which is valid UTF-8. Swallow the pair
        and report no usable input rather than raising UnicodeDecodeError.
        """
        raw = msvcrt.getch()
        if raw in (b"\x00", b"\xe0"):
            msvcrt.getch()  # discard the scan code byte
            return ""
        try:
            return raw.decode("utf-8").lower()
        except UnicodeDecodeError:
            return ""

except ImportError:
    # Unix/Linux/Mac - fallback to regular input for now
    def _getch() -> str:
        """Get input (fallback for non-Windows systems)."""
        return input().strip().lower()


# Lines used by the stats summary panel at the top.
STATS_PANEL_LINES = 3

# Lines used by the expansion prompt at the bottom.
PROMPT_LINES = 2

# Gap between columns when using horizontal layout (Rich Columns default).
COLUMN_GAP = 2

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

# Cell padding a table falls back to when it will not fit
# (top, right, bottom, left).
SNUG_PADDING = (0, 1, 0, 0)
TIGHT_PADDING = (0, 0)
SHORT_HEADERS = {
    "TxnId": "Id",
    "Action": "Act",
    "Amount": "Amt",
    "Units": "Qt.",
    "Ticker": "Tkr",
    "Account": "Acct",
    "Description": "Desc.",
    "Currency": "$",
    "Transactions": "Txns",
    "Settle Updates": "Settles",
    "Transfers": "Txfs",
    "Rejected": "Rej.",
    "Rejection_Reason": "Reason",
    "OldTicker": "Old",
    "NewTicker": "New",
    "EffectiveDate": "Date",
}

THEME_MERGED = "bright_blue"  # Merged panels - informational
THEME_TRANSFORMS = "medium_purple3"  # Transforms - modification
THEME_EXCLUDED = "dark_red"  # Excluded/rejected - removal
THEME_DUPES = "dark_red"  # Duplicates - removal
THEME_SUCCESS = "green4"  # Import summary, imported - success

TRANSACTION_COLORS = {
    Action.BUY: "bright_red",
    Action.SELL: "bright_green",
    Action.DIVIDEND: "bright_blue",
    Action.FXT: "cyan",
    Action.FCH: "yellow",
    Action.CONTRIBUTION: "green",
    Action.WITHDRAWAL: "red",
    Action.ROC: "magenta",
    Action.SPLIT: "purple",
    Action.TFR_IN: "green",
    Action.TFR_OUT: "red",
}


def show_data_table(
    data: list[dict[str, Any]],
    title: str | None = None,
    max_rows: int = 50,
    *,
    theme: str = "bright_blue",
) -> None:
    """Display generic data in a Rich table.

    Args:
        data: List of dictionaries containing data to display
        title: Optional title for the table
        max_rows: Maximum number of rows to display
        theme: Border color theme for the table
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
            table.add_row(*[_safe_str(value) for value in row.values()])

    console_print(fit_padding(table))

    if truncated:
        console_print(
            f"\n[dim]... showing first {max_rows} of {len(data)} items[/dim]",
        )


@dataclass
class Block:
    """A display block representing some renderable content.

    Blocks are measured renderables with metadata for layout optimization.
    They know their exact dimensions and can be arranged by TilingLayout.

    Attributes:
        name: Display name for the block (e.g., "Merged", "Excluded").
        key: Single character key for interactive expansion.
        panel: The Rich renderable (typically a Table) to display.
        total: Total number of items in the underlying data.
        shown: Number of items currently shown in the panel.
        expandable: Whether there are more items than shown (total > shown).
        data_type: Type identifier for expansion handling.
        data: The underlying data (list or DataFrame).
        width: Measured width of the panel in characters.
        height: Measured height of the panel in lines.
        full_width: If True, block is displayed below tiled layout at full width.
    """

    name: str
    key: str
    panel: RenderableType
    total: int
    shown: int
    expandable: bool
    data_type: str
    data: Any
    width: int
    height: int
    full_width: bool = False

    @classmethod
    def create(  # noqa: PLR0917
        cls,
        name: str,
        key: str,
        panel: RenderableType,
        total: int,
        shown: int,
        data_type: str,
        data: Any,
        *,
        full_width: bool = False,
    ) -> Block:
        """Create a Block.

        Args:
            name: Display name for the block.
            key: Single character key for interactive expansion.
            panel: The Rich renderable to display.
            total: Total number of items in the underlying data.
            shown: Number of items currently shown.
            data_type: Type identifier for expansion handling.
            data: The underlying data.
            full_width: If True, block is displayed below tiled layout.

        Returns:
            A new Block instance with measured width and height.
        """
        measurement = Measurement.get(console, console.options, panel)
        width = measurement.maximum

        # Calculate height: for tables, count rows + header/footer overhead
        # We render to count actual lines
        with console.capture() as capture:
            console.print(panel)
        rendered = capture.get()
        height = rendered.count("\n")

        return cls(
            name=name,
            key=key,
            panel=panel,
            total=total,
            shown=shown,
            expandable=total > shown,
            data_type=data_type,
            data=data,
            width=width,
            height=height,
            full_width=full_width,
        )


class TilingLayout:
    """Layout manager that arranges Blocks to maximize terminal space usage.

    TilingLayout optimizes block placement using a bin-packing algorithm:
    1. Separates full-width blocks to be rendered below the tiled layout.
    2. Sorts remaining blocks by height (tallest first) to use as column anchors.
    3. Places the tallest block in the first column to set the height budget.
    4. Stacks shorter blocks vertically in subsequent columns.
    5. Starts new columns when blocks don't fit in remaining vertical space.
    6. Renders full-width blocks at the end, below the tiled columns.
    """

    def __init__(self, blocks: list[Block]) -> None:
        """Initialize the tiling layout.

        Args:
            blocks: List of Block instances to arrange.
        """
        # Separate full-width blocks from tiled blocks
        self.tiled_blocks = [b for b in blocks if not b.full_width]
        self.full_width_blocks = [b for b in blocks if b.full_width]
        self.term_width, self.term_height = _get_terminal_size()
        self._columns: list[list[Block]] = []

    def compute_layout(self) -> list[list[Block]]:
        """Compute optimal column layout for the tiled blocks.

        Algorithm:
        1. Sort blocks by descending height (Tallest first)
        2. The tallest block anchors the first column and sets height budget.
        3. Try to fit remaining blocks into existing columns by stacking.
        4. Create new columns when blocks exceed width or height constraints.

        Returns:
            List of columns, where each column is a list of Blocks to stack.
        """
        if not self.tiled_blocks:
            return []

        sorted_blocks = sorted(self.tiled_blocks, key=lambda b: b.height, reverse=True)

        # First block (tallest) anchors the first column
        self._columns = [[sorted_blocks[0]]]
        column_heights = [sorted_blocks[0].height]
        column_widths = [sorted_blocks[0].width]

        # Reference height is the tallest block's height
        reference_height = sorted_blocks[0].height

        for block in sorted_blocks[1:]:
            placed = False

            # Try to stack in an existing column (prefer columns with most space)
            # Sort column indices by remaining height (most space first)
            column_order = sorted(
                range(len(self._columns)),
                key=lambda i: reference_height - column_heights[i],
                reverse=True,
            )

            for col_idx in column_order:
                remaining_height = reference_height - column_heights[col_idx]

                if block.height <= remaining_height:
                    # !Verify that the column width can accommodate this block
                    total_width = self._calculate_total_width_with_block(
                        column_widths,
                        col_idx,
                        block.width,
                    )
                    if total_width <= self.term_width:
                        self._columns[col_idx].append(block)
                        column_heights[col_idx] += block.height
                        column_widths[col_idx] = max(
                            column_widths[col_idx],
                            block.width,
                        )
                        placed = True
                        break

            if not placed:
                new_total_width = (
                    sum(column_widths) + COLUMN_GAP * len(column_widths) + block.width
                )
                if new_total_width <= self.term_width:
                    # Block fits, make new column ...
                    self._columns.append([block])
                    column_heights.append(block.height)
                    column_widths.append(block.width)
                else:
                    # Block too fat, add to shortest column instead
                    min_height_idx = column_heights.index(min(column_heights))
                    self._columns[min_height_idx].append(block)
                    column_heights[min_height_idx] += block.height
                    column_widths[min_height_idx] = max(
                        column_widths[min_height_idx],
                        block.width,
                    )

        return self._columns

    def _calculate_total_width_with_block(
        self,
        column_widths: list[int],
        update_idx: int,
        new_block_width: int,
    ) -> int:
        """Calculate total layout width if a block is added to a column.

        Args:
            column_widths: Current widths of all columns.
            update_idx: Index of column being updated.
            new_block_width: Width of the block being added.

        Returns:
            Total width including gaps between columns.
        """
        # Calculate width if this block updates the column
        widths = column_widths.copy()
        widths[update_idx] = max(widths[update_idx], new_block_width)
        return sum(widths) + COLUMN_GAP * (len(widths) - 1)

    def render(self) -> None:
        """Render the computed layout to the console.

        Uses Rich's Columns and Group for horizontal and vertical arrangement.
        Full-width blocks are rendered below the tiled columns.
        """
        if not self._columns:
            self._columns = self.compute_layout()

        # Render tiled columns
        if self._columns:
            column_renderables: list[RenderableType] = []

            for column_blocks in self._columns:
                if len(column_blocks) == 1:
                    column_renderables.append(column_blocks[0].panel)
                else:
                    # Stack multiple blocks vertically using Group
                    panels = [b.panel for b in column_blocks]
                    column_renderables.append(Group(*panels))

            if len(column_renderables) == 1:
                console.print(column_renderables[0])
            else:
                cols = Columns(
                    column_renderables,
                    equal=False,
                    expand=False,
                    align="left",
                )
                console.print(cols)

        # Render full-width blocks below the tiled layout
        for block in self.full_width_blocks:
            console.print(block.panel)

    @property
    def all_blocks(self) -> list[Block]:
        """Get all blocks in layout order.

        Returns:
            Flattened list of blocks from all columns, followed by full-width blocks.
        """
        if not self._columns:
            self._columns = self.compute_layout()
        tiled = [block for column in self._columns for block in column]
        return tiled + self.full_width_blocks


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


# Cache Freshness thresholds, in seconds, for `format_freshness`.
_JUST_NOW = 60
_ONE_HOUR = 3600
_ONE_DAY = 86400


def format_freshness(computed_at: datetime | None) -> str:
    """Describe how current a cached figure is, in human terms.

    Every surface that can serve cached data has to say so, in its header or
    panel subtitle.

    Args:
        computed_at: When the data was computed, or None if it was computed by
            this invocation.

    Returns:
        A short phrase such as `computed just now` or `cached 4d ago`.
    """
    if computed_at is None:
        return "computed just now"

    now = datetime.now(computed_at.tzinfo)
    elapsed = max((now - computed_at).total_seconds(), 0)
    if elapsed < _JUST_NOW:
        return "cached just now"
    if elapsed < _ONE_HOUR:
        return f"cached {int(elapsed // 60)}m ago"
    if elapsed < _ONE_DAY:
        return f"cached {int(elapsed // _ONE_HOUR)}h ago"
    return f"cached {int(elapsed // _ONE_DAY)}d ago"


# Freshness icons
_FRESH_ICON, _CACHED_ICON = ("●", "⏱") if supports_unicode() else ("*", "~")


def freshness_badge(computed_at: datetime | None) -> str:
    """Render the freshness indicator as a coloured, iconed one-liner.

    Meant to be printed above table, since footer is invisible while paging.
    (Pager redraws in alternate screen, footer only prints after exited)

    Args:
        computed_at: When the data was computed, or None if it was computed
            by this invocation.

    Returns:
        A Rich-markup string such as `[green]●[/green] computed just now`.
    """
    text = format_freshness(computed_at)
    if computed_at is None:
        return f"[green]{_FRESH_ICON}[/green] [dim]{text}[/dim]"
    return f"[yellow]{_CACHED_ICON}[/yellow] [dim]{text}[/dim]"


def page_frame(
    total_rows: int,
    title: str,
    page_size: int | None,
    render: Callable[[int, int], None],
    badge: str | None = None,
) -> None:
    """Drive the n/p/q pager over a row range, delegating pages to `render`.

    Args:
        total_rows: Number of rows to page through
        title: Title for the display
        page_size: Rows per page. If None, calculated from console height
        render: Called with (start, end) to draw one page of rows
        badge: Optional status line (e.g. from `freshness_badge`) printed
            above every page
    """
    if total_rows == 0:
        console_print(f"[yellow]No transactions to display for {title}[/yellow]")
        return

    # Printed once here for the single-page and test-environment paths below.
    if badge:
        console_print(badge)

    if _is_test_environment():
        render(0, total_rows)
        return

    if page_size is None:
        page_size = _calculate_available_height(table=True, pages=True)

    if total_rows <= page_size:
        render(0, total_rows)
        return

    total_pages = (total_rows + page_size - 1) // page_size
    current_page = 0

    # Alternate screen: each page redraws in a scratch buffer the terminal
    # discards on exit.
    with console.screen():
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

    console.rule("End of Transactions", style="dim")


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

    console.clear()
    console.rule(
        f"{title} - Page {current_page + 1}/{total_pages}",
        style="bright_blue",
    )
    if badge:
        console_print(badge)

    render(start_idx, end_idx)

    console.print(
        f"[dim]Showing transactions {start_idx + 1}-{end_idx} of {total_rows}[/dim]",
    )

    if current_page == 0 and total_pages == 1:
        return current_page, True  # Only one page, no navigation needed

    prompt = _get_pagination_prompt(current_page, total_pages)
    console.print(f"\n{prompt}")

    try:
        user_input = _getch()
        return _handle_pagination_input(user_input, current_page, total_pages)
    except (KeyboardInterrupt, EOFError):
        return current_page, True


def page_transactions(
    df: pd.DataFrame,
    title: str = "Transactions",
    page_size: int | None = None,
    context: TransactionContext = TransactionContext.GENERAL,
) -> None:
    """Display transactions with paging support for large datasets.

    This function displays transaction data in pageable format, allowing
    navigation through large transaction lists without overwhelming the console.

    Args:
        df: DataFrame containing transaction data
        title: Title for the transaction display
        page_size: Number of transactions per page. If None, calculated dynamically
            based on available console height
        context: Transaction context to specify column visibility
    """

    def render(start: int, end: int) -> None:
        page_df = df.iloc[start:end]
        # Only the first page carries the title; later pages get the rule.
        page_title = title if start == 0 and end == len(df) else None
        TransactionDisplay().transactions_table(
            page_df,
            title=page_title,
            max_rows=len(page_df),
            context=context,
        )

    page_frame(len(df), title, page_size, render)


def page_changes(
    before: pd.DataFrame,
    after: pd.DataFrame,
    changed_columns: Sequence[str],
    title: str = "Pending Changes",
    page_size: int | None = None,
) -> None:
    """Display a pending edit as a paged before/after view.

    Args:
        before: Transactions as they are now
        after: The same transactions, positionally aligned, once edited
        changed_columns: Columns the edit touches
        title: Title for the display
        page_size: Rows per page. If None, calculated from console height
    """

    def render(start: int, end: int) -> None:
        page_title = title if start == 0 and end == len(before) else None
        TransactionDisplay().changes_table(
            before.iloc[start:end],
            after.iloc[start:end],
            changed_columns,
            title=page_title,
        )

    page_frame(len(before), title, page_size, render)


def _get_terminal_size() -> tuple[int, int]:
    """Get terminal dimensions (width, height).

    Returns:
        Tuple of (width, height) in characters
    """
    try:
        # Read through the module rather than the imported binding
        term_size = console_module.console.size
    except (AttributeError, OSError):  # pragma: no cover
        return 80, 24  # Sensible fallback defaults
    else:
        return term_size.width, term_size.height


def overflow(table: Table) -> int:
    """If terminal is overflowing, report by how many characters.

    Args:
        table: The table about to be printed.

    Returns:
        Characters by which the table overruns the terminal, or zero if it
        fits.
    """
    active = console_module.console
    # Measured unbounded max vs actual terminal width.
    roomy = active.options.update(max_width=_UNBOUNDED_WIDTH)
    wanted = Measurement.get(active, roomy, table).maximum
    return max(wanted - active.width, 0)


def _snug(table: Table) -> None:
    """Leave a cell a space on its right only, rather than either side."""
    table.padding = Padding.unpack(SNUG_PADDING)


def _tight(table: Table) -> None:
    """Run the columns flush against their borders."""
    table.padding = Padding.unpack(TIGHT_PADDING)


def _shorten_headers(table: Table) -> None:
    """Swap in the short form of every header that has one."""
    for column in table.columns:
        short = SHORT_HEADERS.get(str(column.header))
        if short is not None:
            column.header = short


def fit_padding(table: Table) -> Table:
    """Fit a table to the terminal, giving up the least that it can.

    Args:
        table: The table about to be printed. Adjusted in place.

    Returns:
        The same table, for printing inline.
    """
    for concede in (_snug, _tight, _shorten_headers):
        if not overflow(table):
            break
        concede(table)
    return table


def _calculate_available_height(*, table: bool = False, pages: bool = False) -> int:
    """Calculate available height for content after reserved UI elements."""
    _, height = _get_terminal_size()
    if pages:
        # Paged full-screen table: console.rule + progress line + prompt.
        # No stats panel is shown in this flow.
        reserved_lines = PAGE_RULE_HEIGHT + PAGE_PROGRESS_HEIGHT + PROMPT_LINES
    else:
        # Tiled audit-block view: stats panel + expansion prompt.
        reserved_lines = STATS_PANEL_LINES + PROMPT_LINES
    reserved_lines += TABLE_HEADER_HEIGHT if table else 0
    reserved_lines += SAFETY_MARGIN
    return max(height - reserved_lines, 10)  # Minimum 10 lines


def _is_test_environment() -> bool:
    """Check if code is running in a test environment.

    Returns:
        True if running under pytest, False otherwise.
    """
    return "PYTEST_CURRENT_TEST" in os.environ


def _safe_str(value: Any) -> str:
    """Convert value to string, treating pandas NA as empty string.

    Args:
        value: Value to convert.

    Returns:
        String representation, empty string for NA/None values.
    """
    if pd.isna(value):
        return ""
    return str(value)


class TransactionDisplay:
    """Rich display utilities for transaction data."""

    def __init__(self) -> None:
        """Initialize the transaction display."""
        self.console = Console()

    def _format_amount_display(self, amount: float, action: str) -> str:
        """Format amount with color based on action type.

        Args:
            amount: Transaction amount
            action: Transaction action type

        Returns:
            Formatted amount string with color markup
        """
        amount_str = "0.00" if pd.isna(amount) else f"{float(amount):,.2f}"

        if action in [Action.SELL, Action.CONTRIBUTION] or amount > 0:
            return f"[green]{amount_str}[/green]"
        if action in [Action.BUY, Action.WITHDRAWAL] or amount < 0:
            return f"[red]{amount_str}[/red]"
        return f"[white]{amount_str}[/white]"

    def _parse_amount(self, amount: Any) -> float:
        """Parse amount value to float.

        Args:
            amount: Raw amount value

        Returns:
            Parsed float value or 0.0
        """
        try:
            return float(amount)
        except (TypeError, ValueError):
            return 0.0

    def transactions_table(
        self,
        df: pd.DataFrame,
        title: str | None = None,
        max_rows: int = 50,
        context: TransactionContext = TransactionContext.GENERAL,
        *,
        show: bool = True,
    ) -> Table | None:
        """Display transactions in a Rich table with color coding.

        Args:
            df: DataFrame containing transaction data
            title: Optional title for the table
            max_rows: Maximum number of rows to display
            context: Transaction context to control column visibility
            show: If True, prints the table to console; else returns the Table
        """
        if df.empty:
            console_print("[yellow]No transactions to display[/yellow]")
            return None

        display_df = df.head(max_rows)
        truncated = len(df) > max_rows

        table = Table(
            title=title,
            show_header=True,
            header_style="bold bright_white",
            border_style="bright_blue",
            show_lines=False,
        )

        self._add_table_columns(
            display_df,
            table,
            context,
        )
        self._add_table_rows(
            display_df,
            table,
            context,
        )
        fit_padding(table)

        if not show:
            return table

        console_print(table)

        if truncated:
            console_print(
                f"\n[dim]... showing first {max_rows} of {len(df)} transactions[/dim]",
            )

        return None

    def changes_table(
        self,
        before: pd.DataFrame,
        after: pd.DataFrame,
        changed_columns: Sequence[str],
        title: str | None = "Pending Changes",
        *,
        show: bool = True,
    ) -> Table | None:
        """Show a pending edit as full transactions with inline before/after.

        Args:
            before: Transactions as they are now.
            after: The same transactions, positionally aligned, as they would
                be once the edit is applied.
            changed_columns: Columns the edit touches.
            title: Optional title for the table.
            show: If True, prints the table to console; else returns the Table.

        Returns:
            The Table when `show` is False, otherwise None.
        """
        if before.empty:
            console_print("[yellow]No transactions to display[/yellow]")
            return None

        context = TransactionContext.GENERAL
        columns = _ordered_columns(before, context)
        changed = [column for column in changed_columns if column in columns]

        table = Table(
            title=title,
            show_header=True,
            header_style="bold bright_white",
            border_style=THEME_TRANSFORMS,
            show_lines=False,
        )
        self._add_table_columns(before, table, context, widen=set(changed))

        for position in range(len(before)):
            old_cells = self._row_cells(before.iloc[position], columns)
            new_cells = self._row_cells(after.iloc[position], columns)
            row_data = []
            for column in columns:
                old = old_cells[column]
                new = new_cells[column]
                if column in changed and old != new:
                    row_data.append(
                        f"[red]{old}[/red] [dim]->[/dim] [green]{new}[/green]",
                    )
                else:
                    row_data.append(old)
            table.add_row(*row_data)
        fit_padding(table)

        if not show:
            return table

        console_print(table)
        return None

    def _add_table_columns(
        self,
        display_df: pd.DataFrame,
        table: Table,
        context: TransactionContext,
        widen: Container[str] = (),
    ) -> None:
        """Add columns to transaction table based on context.

        Args:
            display_df: DataFrame containing transaction data
            table: Rich Table to add columns to
            context: Context to determine which columns to show
            widen: Columns needing extra width to hold an inline before/after
                diff, and which therefore wrap instead of truncating
        """
        # Define the set of columns that have special handling
        added_columns = {*_TXN_COLUMN_SPECS, Column.Txn.SETTLE_CALCULATED}

        for column in _ordered_columns(display_df, context):
            spec = _TXN_COLUMN_SPECS.get(column)
            if spec is None:  # non-standard column, handled below
                continue
            width = spec.max_width
            if column in widen:
                # Make room for an inline "old -> new" diff.
                table.add_column(
                    column,
                    style=spec.style,
                    justify=spec.justify,
                    no_wrap=False,
                    min_width=width + _DIFF_EXTRA_WIDTH,
                    max_width=width * 2 + _DIFF_EXTRA_WIDTH,
                )
            else:
                table.add_column(
                    column,
                    style=spec.style,
                    justify=spec.justify,
                    no_wrap=True,
                    max_width=width,
                )

        if context != TransactionContext.GENERAL:
            return

        # Add any additional (non-standard) columns present in the DataFrame
        for col in display_df.columns:
            if col not in added_columns:
                table.add_column(
                    str(col),
                    no_wrap=col not in widen,
                    max_width=15 if col not in widen else 33,
                    overflow="ellipsis" if col not in widen else "fold",
                )

    def _row_cells(self, row: pd.Series, columns: Sequence[str]) -> dict[str, str]:
        """Render one transaction into display strings, keyed by column.

        Args:
            row: A single transaction.
            columns: Columns to render, as given by `_ordered_columns`.

        Returns:
            Mapping of column name to its formatted, markup-bearing cell text.
        """
        action = row.get(Column.Txn.ACTION, "")
        action_color = TRANSACTION_COLORS.get(action, "white")

        # Color-code SettleDate based on whether it was calculated
        settle_date_str = _safe_str(row.get(Column.Txn.SETTLE_DATE, ""))
        settle_color = "orange3" if row.get(Column.Txn.SETTLE_CALCULATED) else "green"

        amount = self._parse_amount(row.get(Column.Txn.AMOUNT, 0))
        fee_val = self._parse_amount(row.get(Column.Txn.FEE, 0))

        settle_display = f"[{settle_color}]{settle_date_str}[/{settle_color}]"
        special: dict[str, str] = {
            Column.Txn.SETTLE_DATE: settle_display,
            Column.Txn.ACTION: f"[{action_color}]{action}[/{action_color}]",
            Column.Txn.AMOUNT: self._format_amount_display(amount, action),
            Column.Txn.FEE: f"{fee_val:.2f}",
        }

        return {
            column: special.get(
                column,
                _safe_str(row.get(column, "")).replace("\n", " "),
            )
            for column in columns
        }

    def _add_table_rows(
        self,
        display_df: pd.DataFrame,
        table: Table,
        context: TransactionContext,
    ) -> None:
        """Add rows to transaction table with conditional formatting.

        Args:
            display_df: DataFrame with rows to display
            table: Rich Table to add rows to
            context: Context to determine which columns to show
        """
        columns = _ordered_columns(display_df, context)
        for _, row in display_df.iterrows():
            cells = self._row_cells(row, columns)
            table.add_row(*(cells[column] for column in columns))

    def show_stats_panel(self, stats: dict[str, int | str]) -> None:
        """Display statistics in a Rich panel.

        Args:
            stats: Dictionary of statistics to display
        """
        stats_text = "\n".join(
            f"[bold]{key}:[/bold] [bright_white]{value}[/bright_white]"
            for key, value in stats.items()
        )
        console_panel(stats_text, title="Stats", style="bright_blue", expand=False)

    def show_import_summary(
        self,
        filename: str,
        results: ImportResults,
    ) -> None:
        """Display import summary with success/error styling.

        Args:
            filename: Name of the imported file
            results: ImportResults object containing import metrics
        """
        imported_count = results.imported_count()
        total_count = results.final_db_count
        success = imported_count > 0

        if success:
            icon = get_symbol("success")
            color = "green"
            status = "SUCCESS"
        else:
            icon = get_symbol("warning")
            color = "yellow"
            status = "NO DATA"

        console_print(
            f"{icon}[{color}]{status}[/{color}]: "
            f"[bold]{filename}[/bold] - "
            f"[bright_white]{imported_count}[/bright_white] transactions imported "
            f"([dim]{total_count} total in database[/dim])",
        )

    def show_import_audit(
        self,
        results: ImportResults,
        *,
        verbose: bool = False,
    ) -> None:
        """Display rich audit summary for an import operation.

        Args:
            results: ImportResults with audit data.
            verbose: If True, includes imported transactions block.
        """
        self._show_stats_panel(results)
        blocks = self._build_audit_blocks(results, verbose=verbose)

        if blocks:
            layout = TilingLayout(blocks)
            layout.render()

        # Handle expandable blocks if needed.
        self._prompt_and_expand_blocks(blocks, results)

    def _show_stats_panel(self, results: ImportResults) -> None:
        """Display stats panel in compact mode with color-coding.

        Color scheme:
        - Read: green (adds transactions)
        - Merged: X red (removed) → Y green (added)
        - Excluded: red (removed)
        - Dupes Rejected: red (removed)
        - Imported: green if matches tally, red with yellow tally if mismatch
        """
        parts: list[str] = []

        read_count = results.read_count()
        parts.append(f"[bold]Read:[/bold] [green]{read_count}[/green]")

        merge_candidates = results.merge_candidates()
        merged_into = results.merged_into()
        if merge_candidates > 0:
            # X red → Y green (no spaces around arrow, arrow in white)
            parts.append(
                f"[bold]Merged:[/bold] [red]{merge_candidates}[/red]->"
                f"[green]{merged_into}[/green]",
            )

        excluded = results.excluded_count()
        if excluded > 0:
            parts.append(f"[bold]Excluded:[/bold] [red]{excluded}[/red]")

        intra = results.intra_rejected_count()
        db = results.db_rejected_count()
        if intra > 0 or db > 0:
            dupe_parts = []
            if intra > 0:
                dupe_parts.append(f"[red]{intra}[/red] intra")
            if db > 0:
                dupe_parts.append(f"[red]{db}[/red] db")
            parts.append(f"[bold]Dupes Rejected:[/bold] {', '.join(dupe_parts)}")

        # Calculate expected tally (net change from merges + exclusions)
        merge_delta = merged_into - merge_candidates
        expected_imported = read_count + merge_delta - excluded - intra - db

        # Import summary with color coding
        imported = results.imported_count()
        if imported == expected_imported:
            parts.append(f"[bold]Imported:[/bold] [green]{imported}[/green]")
        else:
            parts.append(
                f"[bold]Imported:[/bold] [red]{imported}[/red] "
                f"[yellow]({expected_imported})[/yellow]",
            )

        stats_text = "  ".join(parts)
        console_panel(stats_text, title="Stats", style="bright_blue", expand=False)

    def _build_audit_blocks(
        self,
        results: ImportResults,
        *,
        verbose: bool = False,
    ) -> list[Block]:
        """Build Blocks for displaying audit information.

        Args:
            results: ImportResults with audit data.
            verbose: If True, includes imported transactions block.

        Returns:
            List of Blocks to display.
        """
        blocks: list[Block] = []
        max_rows = _calculate_available_height()

        if results.merge_events:
            total = len(results.merge_events)
            # For merge events, calculate how many events fit in max_rows
            # accounting for tree structure (1 merged row + N source rows per event)
            events_to_show: list[Any] = []
            rows_used = 0
            for event in results.merge_events:
                event_rows = 1 + len(event.source_rows)
                if rows_used + event_rows > max_rows and events_to_show:
                    break
                events_to_show.append(event)
                rows_used += event_rows
            shown = len(events_to_show)
            panel = self._build_merge_panel(events_to_show, total)
            blocks.append(
                Block.create(
                    name="Merged",
                    key="m",
                    panel=panel,
                    total=total,
                    shown=shown,
                    data_type="merge",
                    data=results.merge_events,
                ),
            )

        max_rows = _calculate_available_height(table=True)

        if results.transform_events:
            total = len(results.transform_events)
            shown = min(total, max_rows)
            panel = self._build_transform_panel(
                results.transform_events[:shown],
                total,
            )
            blocks.append(
                Block.create(
                    name="Transforms",
                    key="t",
                    panel=panel,
                    total=total,
                    shown=shown,
                    data_type="transform",
                    data=results.transform_events,
                ),
            )

        if not results.excluded_df.empty:
            total = len(results.excluded_df)
            shown = min(total, max_rows)
            panel = self._build_excluded_panel(
                results.excluded_df.head(shown),
                total,
            )
            blocks.append(
                Block.create(
                    name="Excluded",
                    key="e",
                    panel=panel,
                    total=total,
                    shown=shown,
                    data_type="excluded",
                    data=results.excluded_df,
                ),
            )

        if not results.intra_rejected_df.empty:
            total = len(results.intra_rejected_df)
            shown = min(total, max_rows)
            panel = self._build_dupes_panel(
                results.intra_rejected_df.head(shown),
                f"Intra Dupes ({total})",
            )
            blocks.append(
                Block.create(
                    name="Intra Dupes",
                    key="i",
                    panel=panel,
                    total=total,
                    shown=shown,
                    data_type="dupes",
                    data=results.intra_rejected_df,
                ),
            )

        if not results.db_rejected_df.empty:
            total = len(results.db_rejected_df)
            shown = min(total, max_rows)
            panel = self._build_dupes_panel(
                results.db_rejected_df.head(shown),
                f"DB Dupes ({total})",
            )
            blocks.append(
                Block.create(
                    name="DB Dupes",
                    key="d",
                    panel=panel,
                    total=total,
                    shown=shown,
                    data_type="dupes",
                    data=results.db_rejected_df,
                ),
            )

        if verbose and not results.final_df.empty:
            total = len(results.final_df)
            shown = min(total, max_rows)
            panel = self.transactions_table(
                results.final_df.head(shown),
                f"Imported ({total})",
                shown,
                TransactionContext.IMPORT,
                show=False,
            )
            if panel is None:
                return blocks
            blocks.append(
                Block.create(
                    name="Imported",
                    key="v",
                    panel=panel,
                    total=total,
                    shown=shown,
                    data_type="dataframe",
                    data=results.final_df,
                    full_width=True,
                ),
            )

        return blocks

    def _build_merge_panel(
        self,
        events: list[Any],
        total: int,
    ) -> Table:
        """Build a table for merge events.

        Starts from parent transaction nodes to save horizontal space.
        """
        table = Table(
            title=f"Merged ({total})",
            show_header=False,
            border_style=THEME_MERGED,
            expand=False,
            box=None,
            padding=SNUG_PADDING,
        )
        table.add_column("tree")
        tree_content: list[str] = []
        for event in events:
            merged_summary = (
                f"[green]+ {event.merged_row.get(Column.Txn.TXN_DATE, '')}|"
                f"{event.merged_row.get(Column.Txn.ACTION, '')}|"
                f"{event.merged_row.get(Column.Txn.AMOUNT, '')}|"
                f"{event.merged_row.get(Column.Txn.TICKER, '')}[/green]"
            )
            tree_content.append(merged_summary)
            for _, src_row in event.source_rows.iterrows():
                src_summary = (
                    f"  [red]└ {src_row.get(Column.Txn.TXN_DATE, '')}|"
                    f"{src_row.get(Column.Txn.ACTION, '')}|"
                    f"{src_row.get(Column.Txn.AMOUNT, '')}|"
                    f"{src_row.get(Column.Txn.TICKER, '')}[/red]"
                )
                tree_content.append(src_summary)

        for line in tree_content:
            table.add_row(line)

        return table

    def _build_transform_panel(
        self,
        events: list[Any],
        total: int,
    ) -> Table:
        """Build a table for transform events.

        Colors Field column by action color, limits Old/New width to 15.
        """
        table = Table(
            title=f"Transforms ({total})",
            show_header=True,
            header_style="bold",
            border_style=THEME_TRANSFORMS,
            expand=False,
            padding=SNUG_PADDING,
        )
        table.add_column("Field")
        table.add_column("Rows", justify="right", width=4)
        table.add_column("Old", max_width=15, overflow="ellipsis")
        table.add_column("New", max_width=15, overflow="ellipsis")

        for e in events:
            old_val = ",".join(map(str, e.old_values))
            new_val = str(e.new_value)
            # Try to color new_val if it's an Action enum value
            try:
                action = Action(new_val)
                new_val_color = TRANSACTION_COLORS.get(action, "white")
                new_val = f"[{new_val_color}]{new_val}[/{new_val_color}]"
            except ValueError:
                pass
            table.add_row(e.field_name, str(e.row_count), old_val, new_val)

        return table

    def _build_excluded_panel(
        self,
        df: pd.DataFrame,
        total: int,
    ) -> Table:
        """Build a table for excluded transactions.

        Shows TxnDate, Action, Amount always, plus columns referenced in reason.
        Only show essential columns that are relevant to the exclusion reason.
        """
        table = Table(
            title=f"Excluded ({total})",
            show_header=True,
            header_style="bold",
            border_style=THEME_EXCLUDED,
            expand=False,
            padding=SNUG_PADDING,
        )

        cols_to_show: list[str] = [str(c) for c in EXCLUSION_BASE_COLUMNS]

        if Column.REJECTION_REASON in df.columns:
            for reason in df[Column.REJECTION_REASON].dropna().unique():
                reason_str = str(reason).upper()
                # Check if reason references a TXN_ESSENTIALS column
                for col in TXN_ESSENTIALS:
                    col_str = str(col)
                    if col_str.upper() in reason_str and col_str not in cols_to_show:
                        cols_to_show.append(col_str)

            # Always show rejection reason last
            cols_to_show.append(str(Column.REJECTION_REASON))

        for col in cols_to_show:
            max_w = 20 if col == str(Column.REJECTION_REASON) else 12
            table.add_column(str(col), overflow="ellipsis", max_width=max_w)

        for _, row in df.iterrows():
            row_vals = [_safe_str(row.get(c, ""))[:15] for c in cols_to_show]
            table.add_row(*row_vals)

        return table

    def _build_dupes_panel(
        self,
        df: pd.DataFrame,
        title: str,
    ) -> Table:
        """Build a table for duplicate transactions.

        Only shows TXN_ESSENTIALS columns used for duplicate detection.
        Never shows SettleCalculated, SettleDate, Description, etc.
        """
        table = Table(
            title=title,
            show_header=True,
            header_style="bold",
            border_style=THEME_DUPES,
            expand=False,
            padding=SNUG_PADDING,
        )

        available_cols = [c for c in TXN_ESSENTIALS if c in df.columns]

        for col in available_cols:
            table.add_column(str(col), overflow="ellipsis", max_width=15)

        for _, row in df.iterrows():
            row_vals = [_safe_str(row.get(c, ""))[:15] for c in available_cols]
            table.add_row(*row_vals)

        return table

    def _prompt_and_expand_blocks(
        self,
        blocks: list[Block],
        results: ImportResults,
    ) -> None:
        """Show expansion prompt and handle user inputs.

        Args:
            blocks: List of Blocks that are available.
            results: ImportResults for redisplay after expansion.
        """
        expandable = [b for b in blocks if b.expandable]
        if not expandable:
            return

        self._show_block_key_hints(expandable)

        while True:
            try:
                user_input = _getch()
                if user_input == "q":
                    break

                for block in expandable:
                    if user_input == block.key:
                        self._expand_block(block)
                        self._redisplay_audit_blocks(results, blocks)
                        break
            except (KeyboardInterrupt, EOFError):
                break

    def _show_block_key_hints(self, expandable: list[Block]) -> None:
        """Display the expansion key hints prompt for blocks.

        Args:
            expandable: List of expandable blocks.
        """
        key_hints = " ".join(f"[bold]{b.key}[/bold]={b.name}" for b in expandable)
        console_print(
            f"\n[dim]Press {key_hints} to expand, [bold]q[/bold] to continue[/dim]",
        )

    def _redisplay_audit_blocks(
        self,
        results: ImportResults,
        blocks: list[Block],
    ) -> None:
        """Redisplay the audit panel after returning from expansion.

        Args:
            results: ImportResults with audit data.
            blocks: List of Blocks to display.
        """
        console.clear()
        self._show_stats_panel(results)
        if blocks:
            layout = TilingLayout(blocks)
            layout.render()

        expandable = [b for b in blocks if b.expandable]
        if expandable:
            self._show_block_key_hints(expandable)

    def _expand_block(self, block: Block) -> None:
        """Expand a block to show full data with pagination.

        Args:
            block: The Block to expand.
        """
        console.clear()

        if block.data_type == "merge":
            panel = self._build_merge_panel(block.data, block.total)
            console_print(panel)
            console_print("\n[dim]Press any key to return...[/dim]")
            _getch()

        elif block.data_type == "transform":
            panel = self._build_transform_panel(block.data, block.total)
            console_print(panel)
            console_print("\n[dim]Press any key to return...[/dim]")
            _getch()

        elif block.data_type in ("excluded", "dupes", "dataframe"):
            rows = _calculate_available_height(table=True, pages=True)
            page_transactions(
                block.data,
                title=block.name,
                page_size=rows,
                context=TransactionContext.IMPORT,
            )


class ProgressDisplay:
    """Rich progress indicators for long-running operations."""

    @staticmethod
    @contextmanager
    def spinner(
        color: str = "white",
        *,
        transient: bool = True,
    ) -> Generator[Progress]:
        """Create a spinner progress indicator.

        Args:
            color: Color for the spinner and text (e.g., 'blue', 'green', 'red')
            transient: Whether to remove the progress when complete

        Returns:
            Progress instance configured with a spinner
        """
        progress = Progress(
            SpinnerColumn(style=color),
            TextColumn(f"[{color}]{{task.description}}[/{color}]"),
            console=console,
            transient=transient,
        )

        with progress, progress_console_context(progress.console):
            yield progress

    @staticmethod
    @contextmanager
    def bar(
        color: str = "white",
        *,
        transient: bool = False,
    ) -> Generator[Progress]:
        """Create a bar progress display.

        Args:
            color: Color for the progress bar.
            transient: Whether to remove the progress when complete.

        Yields:
            Progress instance configured with a progress bar.
        """
        progress = Progress(
            TextColumn(f"[{color}]{{task.description}}[/{color}]"),
            BarColumn(complete_style=color),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=transient,
        )

        with progress, progress_console_context(progress.console):
            yield progress
