"""Write the audit trail in its set format.

Every entry is a block: a heading naming what happened and the command that did
it, stage lines indented under it, marked rows under each stage, and exactly one
closing line (`DONE ...` or `FAILED: ...`) before the footer.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from typing import TYPE_CHECKING

from app.logging_setup import get_audit_logger

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

INDENT = "  "
FOOTER = "=" * 80


class AuditMark(StrEnum):
    """What happened to the row a line names. Each mark means one thing."""

    READ = ">"  # As read from the source, before ingest
    STORED = "+"  # Stored or added, or the new version of a row
    REMOVED = "-"  # Not stored, removed, or the old version of a row
    CHANGED = "~"  # Changed field by field
    APPROVED = "*"  # An approved duplicate, let through
    VOIDED = "x"  # Voided by a cancellation
    UNCHANGED = "="  # Left as it was
    UNRESOLVED = "?"  # Could not be resolved


_command: ContextVar[str | None] = ContextVar("audit_command", default=None)
_depth: ContextVar[int] = ContextVar("audit_depth", default=0)
_muted: ContextVar[bool] = ContextVar("audit_muted", default=False)


@contextmanager
def audit_command(command: str) -> Generator[None]:
    """Name the command every block written inside was made by."""
    token = _command.set(command)
    try:
        yield
    finally:
        _command.reset(token)


@contextmanager
def nested() -> Generator[None]:
    """Indent everything written inside one level further, under the stage above."""
    token = _depth.set(_depth.get() + 1)
    try:
        yield
    finally:
        _depth.reset(token)


@contextmanager
def muted() -> Generator[None]:
    """Write nothing inside: for work a summary block already accounts for."""
    token = _muted.set(True)
    try:
        yield
    finally:
        _muted.reset(token)


def enabled(level: int = logging.INFO) -> bool:
    """Whether the audit trail records `level`, so costly rows can be skipped."""
    return not _muted.get() and get_audit_logger().isEnabledFor(level)


def describe_backup(backup: Path | None) -> str:
    """Name the backup that undoes a write, for its stage line."""
    return f"backup {backup.name}" if backup is not None else "no backup taken"


def _write(level: int, depth: int, message: str, *args: object) -> None:
    if _muted.get():
        return
    indent = INDENT * (depth + _depth.get())
    get_audit_logger().log(level, f"{indent}{message}", *args)


def stage(message: str, *args: object, level: int = logging.INFO) -> None:
    """Write a stage line: one step of the block, with its rows below it."""
    _write(level, 1, message, *args)


def row(
    mark: AuditMark,
    text: str,
    *,
    depth: int = 2,
    level: int = logging.INFO,
) -> None:
    """Write one marked row under the stage above it."""
    _write(level, depth, "%s %s", mark, text)


class AuditBlock:
    """An open block, closed by `done` or, when the work raises, `FAILED`."""

    def __init__(self) -> None:
        """Start with no closing line written."""
        self.closed = False

    def done(self, message: str, *args: object) -> None:
        """Write the block's closing line, saying what it did."""
        _write(logging.INFO, 0, f"DONE {message}", *args)
        self.closed = True

    def failed(self, reason: object) -> None:
        """Close the block as failed, with the reason."""
        _write(logging.ERROR, 0, "FAILED: %s", reason)
        self.closed = True


@contextmanager
def block(heading: str, *args: object) -> Generator[AuditBlock]:
    """Write a block: its heading now, its closing line and footer at the end.

    A block whose work raises before it closed is closed as `FAILED`, and the
    error goes on. The heading ends with the command that made the change.

    Yields:
        The open block, for its closing line.
    """
    command = _command.get()
    suffix = f" ({command})" if command else ""
    _write(logging.INFO, 0, f"{heading}{suffix}", *args)
    audit_block = AuditBlock()
    try:
        yield audit_block
    except BaseException as error:
        if not audit_block.closed:
            audit_block.failed(str(error) or type(error).__name__)
        raise
    finally:
        _write(logging.INFO, 0, FOOTER)
        _write(logging.INFO, 0, "")
