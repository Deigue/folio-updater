"""Rich display utilities for CLI commands.

This module provides beautiful table formatting, progress indicators, and
consistent styling for the folio CLI using the Rich library.
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


def print_success(message: str) -> None:
    """Print success message with green checkmark.

    Args:
        message: Success message to display
    """
    console.print(f"✅ [green]{message}[/green]")


def print_error(message: str) -> None:
    """Print error message with red X.

    Args:
        message: Error message to display
    """
    console.print(f"❌ [red]{message}[/red]")


def print_warning(message: str) -> None:
    """Print warning message with yellow warning sign.

    Args:
        message: Warning message to display
    """
    console.print(f"⚠️  [yellow]{message}[/yellow]")


def print_info(message: str) -> None:
    """Print info message with blue info icon.

    Args:
        message: Info message to display
    """
    console.print(f"i  [blue]{message}[/blue]")
