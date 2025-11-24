"""Rich display utilities for CLI commands.

This module provides custom display functions for the folio CLI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.tree import Tree

from cli.console import console_panel, console_print
from utils.constants import Action, Column

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
    from models.import_results import ImportResults


console = Console()

# Color scheme for transaction types
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
) -> None:
    """Display transactions with paging support for large datasets.

    This function displays transaction data in pageable format, allowing
    navigation through large transaction lists without overwhelming the console.

    Args:
        df: DataFrame containing transaction data
        title: Title for the transaction display
        page_size: Number of transactions to show per page
    """
    if df.empty:
        console.print(f"[yellow]No transactions to display for {title}[/yellow]")
        return

    total_rows = len(df)
    if total_rows <= page_size:
        display = TransactionDisplay()
        display.show_transactions_table(df, title=title, max_rows=total_rows)
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
        display.show_transactions_table(page_df, max_rows=page_size)

        console.print(
            f"\n[dim]Showing transactions {start_idx + 1}-{end_idx} of"
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


class TransactionDisplay:
    """Rich display utilities for transaction data."""

    def __init__(self) -> None:
        """Initialize the transaction display."""
        self.console = Console()

    def show_transactions_table(
        self,
        df: pd.DataFrame,
        title: str | None = None,
        max_rows: int = 50,
    ) -> None:
        """Display transactions in a Rich table with color coding.

        Args:
            df: DataFrame containing transaction data
            title: Optional title for the table
            max_rows: Maximum number of rows to display
        """
        if df.empty:
            console_print("[yellow]No transactions to display[/yellow]")
            return

        display_df = df.head(max_rows)
        truncated = len(df) > max_rows

        table = Table(
            title=title,
            show_header=True,
            header_style="bold bright_white",
            border_style="bright_blue",
            show_lines=False,
        )

        table.add_column(Column.Txn.TXN_ID, style="dim", width=6)
        table.add_column(Column.Txn.TXN_DATE, width=11)
        table.add_column(Column.Txn.ACTION, width=12)
        table.add_column(Column.Txn.AMOUNT, justify="right", width=12)
        table.add_column(Column.Txn.CURRENCY, width=4)
        table.add_column(Column.Txn.PRICE, justify="right", width=12)
        table.add_column(Column.Txn.UNITS, justify="right", width=12)
        table.add_column(Column.Txn.TICKER, width=10)
        table.add_column(Column.Txn.ACCOUNT, width=15)
        table.add_column(Column.Txn.SETTLE_DATE, width=11)

        # Add rows with conditional formatting
        for _, row in display_df.iterrows():
            action = row.get(Column.Txn.ACTION, "")
            action_color = TRANSACTION_COLORS.get(action, "white")

            # Format amount with color based on action
            amount = row.get(Column.Txn.AMOUNT, 0)
            try:
                amount = float(amount)
            except (TypeError, ValueError):
                amount = 0.00
            amount_str = "0.00" if pd.isna(amount) else f"{float(amount):,.2f}"

            if action in [Action.SELL, Action.CONTRIBUTION] or amount > 0:
                amount_display = f"[green]{amount_str}[/green]"
            elif action in [Action.BUY, Action.WITHDRAWAL] or amount < 0:
                amount_display = f"[red]{amount_str}[/red]"
            else:
                amount_display = f"[white]{amount_str}[/white]"

            table.add_row(
                str(row.get(Column.Txn.TXN_ID, "")),
                str(row.get(Column.Txn.TXN_DATE, "")),
                f"[{action_color}]{action}[/{action_color}]",
                amount_display,
                str(row.get(Column.Txn.CURRENCY, "")),
                str(row.get(Column.Txn.PRICE, "")),
                str(row.get(Column.Txn.UNITS, "")),
                str(row.get(Column.Txn.TICKER, "")),
                str(row.get(Column.Txn.ACCOUNT, "")),
                str(row.get(Column.Txn.SETTLE_DATE, "")),
            )

        console_print(table)

        if truncated:
            console_print(
                f"\n[dim]... showing first {max_rows} of {len(df)} transactions[/dim]",
            )

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
            icon = "✅"
            color = "green"
            status = "SUCCESS"
        else:
            icon = "⚠️"
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
        """Display rich audit summary for an import operation."""
        flow_panel_data = self._build_flow_panel(results)
        self.show_stats_panel(flow_panel_data)
        self._show_merge_tree(results, show_all=verbose)
        self._show_transform_events(results)
        self._show_exclusions(results)
        self._show_duplicate_rejections(results)
        if verbose:
            self._show_verbose_tables(results)

    def _build_flow_panel(self, results: ImportResults) -> dict[str, int | str]:
        flow = results.flow_summary()
        return {
            "Read": flow["Read"],
            "Merge Candidates": flow["Merge Candidates"],
            "Merged Into": flow["Merged Into"],
            "Excluded": flow["Excluded (format)"],
            "Intra Dupes Rejected": flow["Intra Duplicates Rejected"],
            "DB Dupes Rejected": flow["DB Duplicates Rejected"],
            "Imported": flow["Imported"],
        }

    def _show_merge_tree(self, results: ImportResults, *, show_all: bool) -> None:
        if not results.merge_events:
            return
        tree = Tree("[bold bright_blue]Merged Transactions[/bold bright_blue]")
        events = results.merge_events if show_all else results.merge_events[:20]
        for event in events:
            merged_summary = (
                f"[green]+ {event.merged_row.get('TxnDate', '')}|"
                f"{event.merged_row.get('Action', '')}|"
                f"{event.merged_row.get('Amount', '')}|"
                f"{event.merged_row.get('Ticker', '')}"
            )
            parent = tree.add(merged_summary)
            for _, src_row in event.source_rows.iterrows():
                src_summary = (
                    f"[red]- {src_row.get('TxnDate', '')}|"
                    f"{src_row.get('Action', '')}|"
                    f"{src_row.get('Amount', '')}|"
                    f"{src_row.get('Ticker', '')}"
                )
                parent.add(src_summary)
        console_print(tree)

    def _show_transform_events(self, results: ImportResults) -> None:
        if not results.transform_events:
            return
        transform_rows = [
            {
                "Field": e.field_name,
                "Rows": e.row_count,
                "Old": ",".join(map(str, e.old_values)),
                "New": e.new_value,
            }
            for e in results.transform_events
        ]
        if transform_rows:
            self.show_data_table(
                transform_rows,
                title="Transforms Applied",
            )

    def _show_exclusions(self, results: ImportResults) -> None:
        if results.excluded_df.empty:
            return
        excluded_list_raw = results.excluded_df.to_dict(orient="records")
        excluded_rows: list[dict[str, Any]] = [
            {str(k): v for k, v in row.items()} for row in excluded_list_raw
        ]
        self.show_data_table(
            excluded_rows,
            title="Excluded (format validation)",
        )

    def _show_duplicate_rejections(self, results: ImportResults) -> None:
        if not results.intra_rejected_df.empty:
            intra_raw = results.intra_rejected_df.to_dict(
                orient="records",
            )
            intra_preview: list[dict[str, Any]] = [
                {str(k): v for k, v in row.items()} for row in intra_raw
            ]
            self.show_data_table(
                intra_preview,
                title="Intra Duplicates Rejected",
            )
        if not results.db_rejected_df.empty:
            db_raw = results.db_rejected_df.to_dict(orient="records")
            db_preview: list[dict[str, Any]] = [
                {str(k): v for k, v in row.items()} for row in db_raw
            ]
            self.show_data_table(
                db_preview,
                title="DB Duplicates Rejected",
            )

    def _show_verbose_tables(self, results: ImportResults) -> None:
        if not results.final_df.empty:
            page_transactions(
                results.final_df,
                title="Imported Transactions",
            )

    def show_data_table(
        self,
        data: list[dict[str, Any]],
        title: str | None = None,
        max_rows: int = 50,
    ) -> None:
        """Display generic data in a Rich table.

        Args:
            data: List of dictionaries containing data to display
            title: Optional title for the table
            max_rows: Maximum number of rows to display
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
            border_style="bright_blue",
        )

        # Add columns based on first row keys
        if display_data:
            for key in display_data[0]:
                table.add_column(str(key))

            # Add rows
            for row in display_data:
                table.add_row(*[str(value) for value in row.values()])

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
