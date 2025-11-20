"""Unified Rich console interface for all CLI output.

This module provides helpers for all user-facing console output.

Examples:
    console_success("Import completed successfully")
    console_error("Configuration file not found")
    console_info("Processing 150 transactions...")
    console_warning("Some settlements dates are calculated")
"""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel

console = Console()


def console_success(message: str) -> None:
    """Print success message with green checkmark.

    Use for: Successful operations, confirmations, completions.

    Args:
        message: Success message to display to user
    """
    console.print(f"✅ [green]{message}[/green]")


def console_error(message: str) -> None:
    """Print error message with red X.

    Args:
        message: Error message to display to user
    """
    console.print(f"❌ [red]{message}[/red]")


def console_warning(message: str) -> None:
    """Print warning message with yellow warning sign.

    Args:
        message: Warning message to display to user
    """
    console.print(f"⚠️  [yellow]{message}[/yellow]")


def console_info(message: str) -> None:
    """Print info message with info icon.

    Args:
        message: Info message to display to user
    """
    console.print(f"ℹ️  [cyan]{message}[/cyan]")


def console_plain(message: str) -> None:
    """Print plain message without icon or styling.

    Args:
        message: Message to display to user
    """
    console.print(message)


def console_print(message: str, style: str = "") -> None:
    """Print message with optional Rich markup styling.

    Use for: Plain text output, custom styling, or when other console_* methods
    don't fit the use case.

    Args:
        message: Message to display
        style: Optional Rich markup style (e.g., "[bold]", "[red]", "[dim]")
    """
    if style:
        console.print(f"[{style}]{message}[/{style}]")
    else:
        console.print(message)


def console_rule(title: str = "", style: str = "bright_blue") -> None:
    """Print a horizontal rule for section separation.

    Use for: Separating sections of output, creating visual breaks.

    Args:
        title: Optional title to display in the rule
        style: Color/style for the rule
    """
    console.rule(title, style=style)


def console_panel(message: str, title: str = "", style: str = "bright_blue") -> None:
    """Print message in a Rich panel for emphasis.

    Use for: Important announcements, summaries, highlighted information.

    Args:
        message: Content to display in panel
        title: Optional panel title
        style: Border style/color for the panel
    """
    panel = Panel(
        message,
        title=f"[bold {style}]{title}[/bold {style}]" if title else None,
        border_style=style,
        padding=(0, 1),
    )
    console.print(panel)
