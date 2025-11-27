"""Rich display utilities for CLI commands.

This module provides custom display functions for the folio CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pandas as pd
from rich.columns import Columns
from rich.console import Console, Group, RenderableType
from rich.measure import Measurement
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from cli.console import console_panel, console_print, get_symbol
from utils.constants import TXN_ESSENTIALS, Action, Column, TransactionContext

# Minimum columns always shown for exclusions
EXCLUSION_BASE_COLUMNS = [Column.Txn.TXN_DATE, Column.Txn.ACTION, Column.Txn.AMOUNT]

# Cross-platform single character input
try:
    import msvcrt  # Windows

    def _getch() -> str:
        """Get a single character from stdin without pressing Enter."""
        return msvcrt.getch().decode("utf-8").lower()

except ImportError:
    # Unix/Linux/Mac - fallback to regular input for now
    def _getch() -> str:
        """Get input (fallback for non-Windows systems)."""
        return input().strip().lower()


if TYPE_CHECKING:  # pragma: no cover
    from models import ImportResults


console = Console()

# Lines used by the stats summary panel at the top.
STATS_PANEL_LINES = 3

# Lines used by the expansion prompt at the bottom.
PROMPT_LINES = 2

# Gap between columns when using horizontal layout (Rich Columns default).
COLUMN_GAP = 2

# Header row height for tables.
TABLE_HEADER_HEIGHT = 2

# Page progress display height (e.g., "Showing transactions 1-15 of 100").
PAGE_PROGRESS_HEIGHT = 1

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
}


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
    def create(
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


def page_transactions(
    df: pd.DataFrame,
    title: str = "Transactions",
    page_size: int = 50,
    context: TransactionContext = TransactionContext.GENERAL,
) -> None:
    """Display transactions with paging support for large datasets.

    This function displays transaction data in pageable format, allowing
    navigation through large transaction lists without overwhelming the console.

    Args:
        df: DataFrame containing transaction data
        title: Title for the transaction display
        page_size: Number of transactions to show per page
        context: Transaction context to specify column visibility
    """
    if df.empty:
        console.print(f"[yellow]No transactions to display for {title}[/yellow]")
        return

    total_rows = len(df)
    if total_rows <= page_size:
        display = TransactionDisplay()
        display.transactions_table(
            df,
            title=title,
            max_rows=total_rows,
            context=context,
        )
        return

    total_pages = (total_rows + page_size - 1) // page_size
    current_page = 0

    while True:
        # Calculate slice for current page
        start_idx = current_page * page_size
        end_idx = min(start_idx + page_size, total_rows)
        page_df = df.iloc[start_idx:end_idx]

        console.clear()
        console.rule(
            f"{title} - Page {current_page + 1}/{total_pages}",
            style="bright_blue",
        )

        display = TransactionDisplay()
        display.transactions_table(page_df, max_rows=page_size, context=context)

        console.print(
            f"[dim]Showing transactions {start_idx + 1}-{end_idx} of"
            f" {total_rows}[/dim]",
        )

        if current_page == 0 and total_pages == 1:
            break  # Only one page, no navigation needed

        prompt = _get_pagination_prompt(current_page, total_pages)
        console.print(f"\n{prompt}")

        try:
            user_input = _getch()
            current_page, should_exit = _handle_pagination_input(
                user_input,
                current_page,
                total_pages,
            )
            if should_exit:
                break
        except (KeyboardInterrupt, EOFError):
            break

    console.rule("End of Transactions", style="dim")


def _get_terminal_size() -> tuple[int, int]:
    """Get terminal dimensions (width, height).

    Returns:
        Tuple of (width, height) in characters
    """
    try:
        term_size = console.size
    except (AttributeError, OSError):  # pragma: no cover
        return 80, 24  # Sensible fallback defaults
    else:
        return term_size.width, term_size.height


def _calculate_available_height(*, table: bool = False, pages: bool = False) -> int:
    """Calculate available height for content after reserved UI elements."""
    _, height = _get_terminal_size()
    reserved_lines = STATS_PANEL_LINES + PROMPT_LINES
    reserved_lines += TABLE_HEADER_HEIGHT if table else 0
    reserved_lines += PAGE_PROGRESS_HEIGHT if pages else 0
    return max(height - reserved_lines, 10)  # Minimum 10 lines


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

        if not show:
            return table

        console_print(table)

        if truncated:
            console_print(
                f"\n[dim]... showing first {max_rows} of {len(df)} transactions[/dim]",
            )

        return None

    def _add_table_columns(
        self,
        display_df: pd.DataFrame,
        table: Table,
        context: TransactionContext,
    ) -> None:
        """Add columns to transaction table based on context.

        Args:
            display_df: DataFrame containing transaction data
            table: Rich Table to add columns to
            context: Context to determine which columns to show
        """
        added_columns = {
            Column.Txn.TXN_DATE,
            Column.Txn.ACTION,
            Column.Txn.AMOUNT,
            Column.Txn.CURRENCY,
            Column.Txn.PRICE,
            Column.Txn.UNITS,
            Column.Txn.TICKER,
            Column.Txn.ACCOUNT,
        }

        if context != TransactionContext.IMPORT:
            table.add_column(Column.Txn.TXN_ID, style="dim", max_width=6)
            added_columns.add(Column.Txn.TXN_ID)
        if context == TransactionContext.SETTLEMENT:
            table.add_column(Column.Txn.SETTLE_DATE, max_width=10)
            added_columns.add(Column.Txn.SETTLE_DATE)
        table.add_column(Column.Txn.TXN_DATE, max_width=10)
        table.add_column(Column.Txn.ACTION, max_width=12)
        table.add_column(Column.Txn.AMOUNT, justify="right", max_width=10)
        table.add_column(Column.Txn.CURRENCY, max_width=4)
        table.add_column(Column.Txn.PRICE, justify="right", max_width=10)
        table.add_column(Column.Txn.UNITS, justify="right", max_width=10)
        table.add_column(Column.Txn.TICKER, max_width=12)
        table.add_column(Column.Txn.ACCOUNT, max_width=15)

        if context != TransactionContext.GENERAL:
            return

        # Add any additional columns present in display_df that aren't already added
        for col in display_df.columns:
            if col not in added_columns:
                table.add_column(
                    str(col),
                    width=15,
                    overflow="fold",
                )

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
        # Track which columns we handle specially
        standard_columns = {
            Column.Txn.TXN_ID,
            Column.Txn.TXN_DATE,
            Column.Txn.ACTION,
            Column.Txn.AMOUNT,
            Column.Txn.CURRENCY,
            Column.Txn.PRICE,
            Column.Txn.UNITS,
            Column.Txn.TICKER,
            Column.Txn.ACCOUNT,
            Column.Txn.SETTLE_DATE,
        }

        for _, row in display_df.iterrows():
            action = row.get(Column.Txn.ACTION, "")
            action_color = TRANSACTION_COLORS.get(action, "white")

            amount = self._parse_amount(row.get(Column.Txn.AMOUNT, 0))
            amount_display = self._format_amount_display(amount, action)

            row_data = []
            if context != TransactionContext.IMPORT:
                row_data.append(_safe_str(row.get(Column.Txn.TXN_ID, "")))
            if context == TransactionContext.SETTLEMENT:
                row_data.append(_safe_str(row.get(Column.Txn.SETTLE_DATE, "")))
            row_data.extend(
                [
                    _safe_str(row.get(Column.Txn.TXN_DATE, "")),
                    f"[{action_color}]{action}[/{action_color}]",
                    amount_display,
                    _safe_str(row.get(Column.Txn.CURRENCY, "")),
                    _safe_str(row.get(Column.Txn.PRICE, "")),
                    _safe_str(row.get(Column.Txn.UNITS, "")),
                    _safe_str(row.get(Column.Txn.TICKER, "")),
                    _safe_str(row.get(Column.Txn.ACCOUNT, "")),
                ],
            )

            if context == TransactionContext.GENERAL:
                # Add any additional columns not in the standard set
                row_data.extend(
                    [
                        _safe_str(row.get(col, ""))
                        for col in display_df.columns
                        if col not in standard_columns
                    ],
                )

            table.add_row(*row_data)

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
            f"{icon} [{color}]{status}[/{color}]: "
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

    def show_data_table(
        self,
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

        console_print(table)

        if truncated:
            console_print(
                f"\n[dim]... showing first {max_rows} of {len(data)} items[/dim]",
            )


class ProgressDisplay:
    """Rich progress indicators for long-running operations."""

    @staticmethod
    def spinner_progress(
        color: str = "white",
        *,
        transient: bool = True,
    ) -> Progress:
        """Create a spinner progress indicator.

        Args:
            color: Color for the spinner and text (e.g., 'blue', 'green', 'red')
            transient: Whether to remove the progress when complete

        Returns:
            Progress instance configured with a spinner
        """
        return Progress(
            SpinnerColumn(style=color),
            TextColumn(f"[{color}]{{task.description}}[/{color}]"),
            console=console,
            transient=transient,
        )
