"""Unified Rich console interface for all CLI output.

This module provides helpers for all user-facing console output.

Examples:
    console_success("Import completed successfully")
    console_error("Configuration file not found")
    console_info("Processing 150 transactions...")
    console_warning("Some settlements dates are calculated")
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.panel import Panel

if TYPE_CHECKING:
    from collections.abc import Generator

console = Console()

# Thread-local context for progress console during operations
_progress_console: Console | None = None


@contextmanager
def progress_console_context(progress_console: Console) -> Generator[None]:
    """Context manager to set the progress console for coordinated output.

    Use this to ensure console messages print through the progress console
    during long-running operations, preventing visual artifacts.

    Args:
        progress_console: Console instance from Progress object

    Example:
        with Progress(...) as progress:
            with progress_console_context(progress.console):
                console_info("Processing...")  # Uses progress console
    """
    global _progress_console
    old_console = _progress_console
    _progress_console = progress_console
    try:
        yield
    finally:
        _progress_console = old_console


def _get_output_console() -> Console:
    """Get the active console for output (progress or default).

    Returns:
        Current progress console if active, otherwise default console
    """
    return _progress_console if _progress_console is not None else console


def console_success(message: str) -> None:
    """Print success message with green checkmark.

    Use for: Successful operations, confirmations, completions.

    Args:
        message: Success message to display to user
    """
    _get_output_console().print(f"✅ [green]{message}[/green]")


def console_error(message: str) -> None:
    """Print error message with red X.

    Args:
        message: Error message to display to user
    """
    _get_output_console().print(f"❌ [red]{message}[/red]")


def console_warning(message: str) -> None:
    """Print warning message with yellow warning sign.

    Args:
        message: Warning message to display to user
    """
    _get_output_console().print(f"⚠️  [yellow]{message}[/yellow]")


def console_info(message: str) -> None:
    """Print info message with info icon.

    Args:
        message: Info message to display to user
    """
    _get_output_console().print(f"ℹ️  [cyan]{message}[/cyan]")


def console_plain(message: str) -> None:
    """Print plain message without icon or styling.

    Args:
        message: Message to display to user
    """
    _get_output_console().print(message)


def console_print(message: Any, style: str = "") -> None:
    """Print message with optional Rich markup styling.

    Use for: Plain text output, custom styling, or when other console_* methods
    don't fit the use case.

    Args:
        message: Message to display
        style: Optional Rich markup style (e.g., "[bold]", "[red]", "[dim]")
    """
    out_console = _get_output_console()
    if style:
        out_console.print(f"[{style}]{message}[/{style}]")
    else:
        out_console.print(message)


def console_rule(title: str = "", style: str = "bright_blue") -> None:
    """Print a horizontal rule for section separation.

    Use for: Separating sections of output, creating visual breaks.

    Args:
        title: Optional title to display in the rule
        style: Color/style for the rule
    """
    _get_output_console().rule(title, style=style)


def console_panel(
    message: str,
    title: str = "",
    style: str = "bright_blue",
    *,
    expand: bool = False,
) -> None:
    """Print message in a Rich panel for emphasis.

    Use for: Important announcements, summaries, highlighted information.

    Args:
        message: Content to display in panel
        title: Optional panel title
        style: Border style/color for the panel
        expand: Whether to expand the panel to fill the console
    """
    panel = Panel(
        message,
        title=f"[bold {style}]{title}[/bold {style}]" if title else None,
        border_style=style,
        padding=(0, 1),
        expand=expand,
    )
    _get_output_console().print(panel)
