"""Unified Rich console interface for all CLI output.

This module provides helpers for all user-facing console output.

Examples:
    console_success("Import completed successfully")
    console_error("Configuration file not found")
    console_info("Processing 150 transactions...")
    console_warning("Some settlements dates are calculated")
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.panel import Panel

if TYPE_CHECKING:
    from collections.abc import Generator


def _supports_unicode() -> bool:  # pragma: no cover
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

# Symbols include trailing space for consistent formatting
# Note: VS16 emojis (warning, info) need double space for proper terminal rendering
_SYMBOLS = {
    "success": "✅ " if _UNICODE_SUPPORTED else "[OK] ",
    "error": "❌ " if _UNICODE_SUPPORTED else "[ERROR] ",
    "warning": "⚠️  " if _UNICODE_SUPPORTED else "[WARN] ",
    "info": "ℹ️  " if _UNICODE_SUPPORTED else "[INFO] ",
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
    symbol = _SYMBOLS["success"]
    active_console().print(f"{symbol}[green]{message}[/green]")


def console_error(message: str) -> None:
    """Print error message with red X.

    Args:
        message: Error message to display to user
    """
    symbol = _SYMBOLS["error"]
    active_console().print(f"{symbol}[red]{message}[/red]")


def console_warning(message: str) -> None:
    """Print warning message with yellow warning sign.

    Args:
        message: Warning message to display to user
    """
    symbol = _SYMBOLS["warning"]
    active_console().print(f"{symbol}[yellow]{message}[/yellow]")


def console_info(message: str) -> None:
    """Print info message with info icon.

    Args:
        message: Info message to display to user
    """
    symbol = _SYMBOLS["info"]
    active_console().print(f"{symbol}[cyan]{message}[/cyan]")


def supports_unicode() -> bool:
    """Whether the console can render non-ASCII symbols.

    Returns:
        True when the terminal's encoding handles Unicode, so callers can pick
        a glyph rather than an ASCII stand-in.
    """
    return _UNICODE_SUPPORTED


def get_symbol(symbol_type: str) -> str:
    """Get a Unicode-safe symbol for the given type.

    Args:
        symbol_type: One of 'success', 'error', 'warning', 'info'

    Returns:
        Unicode symbol if supported, ASCII fallback otherwise
    """
    return _SYMBOLS.get(symbol_type, "[?]")


def console_print(message: Any, style: str = "") -> None:
    """Print message with optional Rich markup styling.

    Use for: Plain text output, custom styling, or when other console_* methods
    don't fit the use case.

    Args:
        message: Message to display
        style: Optional Rich markup style (e.g., "[bold]", "[red]", "[dim]")
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
