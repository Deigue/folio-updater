"""Terminal related functions."""

from __future__ import annotations

from term.console import active_console


def terminal_size() -> tuple[int, int]:
    """Get terminal dimensions (width, height).

    Returns:
        Tuple of (width, height) in characters
    """
    try:
        term_size = active_console().size
    except (AttributeError, OSError):
        return 80, 24  # Sensible fallback defaults
    else:
        return term_size.width, term_size.height
