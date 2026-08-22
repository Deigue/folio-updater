"""Test console for capturing plain text output from Rich."""

from __future__ import annotations

from contextlib import contextmanager
from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console

from ui.console import override_console

if TYPE_CHECKING:
    from collections.abc import Generator


# Standardized width for consistent test output.
DEFAULT_WIDTH = 120

# Wide enough that `fit_table` never has to concede anything.
UNCONSTRAINED_WIDTH = 400


class CapturingConsole:
    """A Rich Console that records plain text output to a string buffer."""

    def __init__(self, width: int = DEFAULT_WIDTH) -> None:
        """Initialize the capturing console.

        Args:
            width: Terminal width to render at.
        """
        self.file = StringIO()
        self.console = Console(
            file=self.file,
            force_terminal=False,
            legacy_windows=True,  # Ensures no legacy ANSI codes on Windows
            width=width,
        )

    def get_text(self) -> str:
        """Get the captured plain text output."""
        return self.file.getvalue()


@contextmanager
def capture_output(width: int = DEFAULT_WIDTH) -> Generator[CapturingConsole]:
    """Context manager to capture console output in plain text.

    Args:
        width: Terminal width to render at. Pass `UNCONSTRAINED_WIDTH` when the
            test is about the figures rather than how they were squeezed.

    Yields:
        CapturingConsole: The console instance holding the captured output.

    Example:
        with capture_output() as bio:
            console_success("This is a test.")
            output = bio.get_text()
            assert "This is a test." in output
    """
    capturing = CapturingConsole(width)
    with override_console(capturing.console):
        yield capturing
