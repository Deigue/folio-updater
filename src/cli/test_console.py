"""Test console for capturing plain text output from Rich."""

from __future__ import annotations

from contextlib import contextmanager
from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console

from cli import console as console_module

if TYPE_CHECKING:
    from collections.abc import Generator


class TestConsole:
    """A Rich Console that records plain text output to a string buffer."""

    def __init__(self) -> None:
        """Initialize the test console."""
        self.file = StringIO()
        self.console = Console(
            file=self.file,
            force_terminal=False,
            legacy_windows=True,  # Ensures no legacy ANSI codes on Windows
            width=120,  # Standardized width for consistent test output
        )

    def get_text(self) -> str:
        """Get the captured plain text output."""
        return self.file.getvalue()


@contextmanager
def capture_output() -> Generator[TestConsole]:
    """Context manager to capture console output in plain text.

    Yields:
        TestConsole: The test console instance with the captured output.

    Example:
        with capture_output() as bio:
            console_success("This is a test.")
            output = bio.get_text()
            assert "This is a test." in output
    """
    original_console = console_module.console
    test_console_wrapper = TestConsole()
    console_module.console = test_console_wrapper.console
    try:
        yield test_console_wrapper
    finally:
        console_module.console = original_console
