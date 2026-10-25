"""Unified Rich console interface for all CLI output.

This module provides helpers for all user-facing console output.

Examples:
    console_success("Import completed successfully")
    console_error("Configuration file not found")
    console_info("Processing 150 transactions...")
    console_warning("Some settlement dates are calculated")
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.panel import Panel

if TYPE_CHECKING:
    from collections.abc import Generator


def _supports_unicode() -> bool:
    """Check if the current console supports Unicode characters.

    Returns:
        True if Unicode is supported, False if we should use ASCII fallbacks
    """
    if sys.platform == "win32":
        encoding = getattr(sys.stdout, "encoding", "ascii").lower()
        if encoding in ("cp1252", "cp437", "ascii"):
            return False

        # Try to encode a test Unicode character
        try:
            "✅".encode(encoding)
        except (UnicodeEncodeError, LookupError):
            return False
        else:
            return True

    return True


_UNICODE_SUPPORTED = _supports_unicode()


class StatusMark(StrEnum):
    """The status marks a line can open with."""

    SUCCESS = "success"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


_SYMBOLS = {
    StatusMark.SUCCESS: "✅ " if _UNICODE_SUPPORTED else "[OK] ",
    StatusMark.ERROR: "❌ " if _UNICODE_SUPPORTED else "[ERROR] ",
    StatusMark.WARNING: "⚠  " if _UNICODE_SUPPORTED else "[WARN] ",
    StatusMark.INFO: "ℹ  " if _UNICODE_SUPPORTED else "[INFO] ",
}

# The default console. Callers reach it through `active_console()`
_console = Console(legacy_windows=(sys.platform == "win32" and not _UNICODE_SUPPORTED))

_override: Console | None = None


@contextmanager
def override_console(replacement: Console) -> Generator[None]:
    """Redirect all console output through `replacement` for the duration.

    Use this to coordinate output with a live display (so messages print
    through a Progress object's console rather than fighting it for the
    terminal), or to capture output in tests.

    Args:
        replacement: Console to route output through

    Yields:
        None, with `replacement` installed as the active console.

    Example:
        with Progress(...) as progress:
            with override_console(progress.console):
                console_info("Processing...")  # Uses progress console
    """
    global _override
    previous = _override
    _override = replacement
    try:
        yield
    finally:
        _override = previous


def active_console() -> Console:
    """Get the console that output should go to right now.

    Returns:
        The overriding console if one is installed, otherwise the default.
    """
    return _override if _override is not None else _console


def console_success(message: str) -> None:
    """Print success message with green checkmark.

    Use for: Successful operations, confirmations, completions.

    Args:
        message: Success message to display to user
    """
    symbol = _SYMBOLS[StatusMark.SUCCESS]
    active_console().print(f"{symbol}[green]{message}[/green]")


def console_error(message: str) -> None:
    """Print error message with red X.

    Args:
        message: Error message to display to user
    """
    symbol = _SYMBOLS[StatusMark.ERROR]
    active_console().print(f"{symbol}[red]{message}[/red]")


def console_warning(message: str) -> None:
    """Print warning message with yellow warning sign.

    Args:
        message: Warning message to display to user
    """
    symbol = _SYMBOLS[StatusMark.WARNING]
    active_console().print(f"{symbol}[yellow]{message}[/yellow]")


def console_info(message: str) -> None:
    """Print info message with info icon.

    Args:
        message: Info message to display to user
    """
    symbol = _SYMBOLS[StatusMark.INFO]
    active_console().print(f"{symbol}[cyan]{message}[/cyan]")


def supports_unicode() -> bool:
    """Whether the console can render non-ASCII symbols.

    Returns:
        True when the terminal's encoding handles Unicode, so callers can pick
        a glyph rather than an ASCII stand-in.
    """
    return _UNICODE_SUPPORTED


def get_symbol(mark: StatusMark) -> str:
    """Get a Unicode-safe symbol for the given mark.

    Args:
        mark: Which status mark to get.

    Returns:
        Unicode symbol if supported, ASCII fallback otherwise
    """
    return _SYMBOLS[mark]


def console_print(message: Any, style: str = "") -> None:
    """Print message with optional Rich markup styling.

    Use for: Plain text output, custom styling, or when other console_* methods
    don't fit the use case.

    Args:
        message: Message to display
        style: Optional Rich style name (e.g., "bold", "red", "dim"). The
            brackets are added here.
    """
    out_console = active_console()
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
    active_console().rule(title, style=style)


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
    active_console().print(panel)
