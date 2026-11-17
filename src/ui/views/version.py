"""Rendering `folio version`: where each file is kept and when it was last written."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from rich.markup import escape

from term import console_info, console_print
from ui.format import stamped

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from pathlib import Path


class PathTime(NamedTuple):
    """One path `folio version` lists, and when it was last written.

    Attributes:
        label: What the path holds, such as `config path`.
        path: Where it is.
        written_at: When it was last written, or None when nothing is there yet.
        verb: How its time reads, such as `modified` or `last backup`.
        missing: What to say when nothing is there yet.
    """

    label: str
    path: Path
    written_at: datetime | None
    verb: str = "modified"
    missing: str = "not created yet"


def show_version(version: str, paths: Sequence[PathTime]) -> None:
    """Print the version, then each path with its time lined up in one column."""
    width = max(len(f"{row.label}: {row.path}") for row in paths)
    console_print(f"folio-updater version: {version}")
    for row in paths:
        line = escape(f"{row.label}: {row.path}".ljust(width))
        when = (
            row.missing if row.written_at is None else stamped(row.written_at, row.verb)
        )
        console_info(f"{line}  [dim]{when}[/dim]")
