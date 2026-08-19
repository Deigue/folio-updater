"""Spinners and progress bars for long-running operations."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)

from ui.console import console, progress_console_context

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Generator


class ProgressDisplay:
    """Rich progress indicators for long-running operations."""

    @staticmethod
    @contextmanager
    def spinner(
        color: str = "white",
        *,
        transient: bool = True,
    ) -> Generator[Progress]:
        """Create a spinner progress indicator.

        Args:
            color: Color for the spinner and text (e.g., 'blue', 'green', 'red')
            transient: Whether to remove the progress when complete

        Returns:
            Progress instance configured with a spinner
        """
        progress = Progress(
            SpinnerColumn(style=color),
            TextColumn(f"[{color}]{{task.description}}[/{color}]"),
            console=console,
            transient=transient,
        )

        with progress, progress_console_context(progress.console):
            yield progress

    @staticmethod
    @contextmanager
    def bar(
        color: str = "white",
        *,
        transient: bool = False,
    ) -> Generator[Progress]:
        """Create a bar progress display.

        Args:
            color: Color for the progress bar.
            transient: Whether to remove the progress when complete.

        Yields:
            Progress instance configured with a progress bar.
        """
        progress = Progress(
            TextColumn(f"[{color}]{{task.description}}[/{color}]"),
            BarColumn(complete_style=color),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=transient,
        )

        with progress, progress_console_context(progress.console):
            yield progress
