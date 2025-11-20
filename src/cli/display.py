"""Rich display utilities for CLI commands.

This module provides custom display functions for the folio CLI.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

console = Console()

# Color scheme for transaction types
TRANSACTION_COLORS = {
    "BUY": "bright_red",
    "SELL": "bright_green",
    "DIVIDEND": "bright_blue",
    "FCH": "cyan",
    "FEE": "yellow",
    "CONTRIBUTION": "green",
    "WITHDRAWAL": "red",
    "ROC": "magenta",
    "SPLIT": "purple",
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
    page_size: int = 20,
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
            user_input = input().strip().lower()
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
            self.console.print("[yellow]No transactions to display[/yellow]")
            return

        # Limit rows for readability
        display_df = df.head(max_rows)
        truncated = len(df) > max_rows

        table = Table(
            title=title,
            show_header=True,
            header_style="bold bright_white",
            border_style="bright_blue",
            show_lines=True,
        )

        # Add columns with appropriate styling
        table.add_column("ID", style="dim", width=6)
        table.add_column("Date", width=12)
        table.add_column("Action", width=12)
        table.add_column("Ticker", width=10)
        table.add_column("Amount", justify="right", width=12)
        table.add_column("Curr", width=4)
        table.add_column("Settle Date", width=12)
        table.add_column("Account", width=10)

        # Add rows with conditional formatting
        for _, row in display_df.iterrows():
            action = str(row.get("Action", ""))
            action_color = TRANSACTION_COLORS.get(action, "white")

            # Format amount with color based on action
            amount = row.get("Amount", 0)
            amount_str = "0.00" if pd.isna(amount) else f"{float(amount):,.2f}"

            # Color coding for amounts
            if action in ["SELL", "CONTRIBUTION"] or amount > 0:
                amount_display = f"[green]+{amount_str}[/green]"
            elif action in ["BUY", "WITHDRAWAL"] or amount < 0:
                amount_display = f"[red]-{amount_str}[/red]"
            else:
                amount_display = f"[white]{amount_str}[/white]"

            table.add_row(
                str(row.get("TxnId", "")),
                str(row.get("TxnDate", "")),
                f"[{action_color}]{action}[/{action_color}]",
                str(row.get("Ticker", "") or ""),
                amount_display,
                str(row.get("Currency", "")),
                str(row.get("SettleDate", "")),
                str(row.get("Account", "")),
            )

        self.console.print(table)

        if truncated:
            self.console.print(
                f"\n[dim]... showing first {max_rows} of {len(df)} transactions[/dim]",
            )

    def show_statistics_panel(self, stats: dict[str, int | str]) -> None:
        """Display statistics in a Rich panel.

        Args:
            stats: Dictionary of statistics to display
        """
        stats_text = "\n".join(
            f"[bold]{key}:[/bold] [bright_white]{value}[/bright_white]"
            for key, value in stats.items()
        )

        panel = Panel(
            stats_text,
            title="[bold bright_blue]Statistics[/bold bright_blue]",
            border_style="bright_blue",
            padding=(0, 1),
        )
        self.console.print(panel)

    def show_import_summary(
        self,
        filename: str,
        imported_count: int,
        total_count: int,
        *,
        success: bool = True,
    ) -> None:
        """Display import summary with success/error styling.

        Args:
            filename: Name of the imported file
            imported_count: Number of transactions imported
            total_count: Total transactions in database after import
            success: Whether the import was successful
        """
        if success:
            icon = "✅"
            color = "green"
            status = "SUCCESS"
        else:
            icon = "❌"
            color = "red"
            status = "FAILED"

        self.console.print(
            f"{icon} [{color}]{status}[/{color}]: "
            f"[bold]{filename}[/bold] - "
            f"[bright_white]{imported_count}[/bright_white] transactions imported "
            f"([dim]{total_count} total in database[/dim])",
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
            self.console.print("[yellow]No data to display[/yellow]")
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

        self.console.print(table)

        if truncated:
            self.console.print(
                f"\n[dim]... showing first {max_rows} of {len(data)} items[/dim]",
            )


class ProgressDisplay:
    """Rich progress indicators for long-running operations."""

    @staticmethod
    def file_import_progress() -> Progress:
        """Create a progress bar for file imports.

        Returns:
            Progress instance configured for file operations
        """
        return Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,  # Removes when complete
        )

    @staticmethod
    def api_download_progress() -> Progress:
        """Create a spinner for API downloads.

        Returns:
            Progress instance configured for API operations
        """
        return Progress(
            SpinnerColumn(style="blue"),
            TextColumn("[blue]{task.description}[/blue]"),
            console=console,
            transient=True,
        )
