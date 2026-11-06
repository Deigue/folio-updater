"""Write the audit trail in its set format.

Every entry has a heading naming what happened and the command that did it,
stage lines indented under it, marked rows under each stage, and exactly one
closing line (`DONE ...` or `FAILED: ...`) before the footer.

The heading is held back until a line under it is recorded, and is written at
that line's level. So when `audit_log_level` is raised, the warnings and failures
that remain still sit under their heading and footer.
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
_open: ContextVar[AuditEntry | None] = ContextVar("audit_entry", default=None)


@contextmanager
def audit_command(command: str) -> Generator[None]:
    """Name the command every entry written inside was made by."""
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
    """Write nothing inside: for work a summary entry already accounts for."""
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
    if not get_audit_logger().isEnabledFor(level):
        return
    entry = _open.get()
    if entry is not None:
        entry.announce(level)
    indent = INDENT * (depth + _depth.get())
    get_audit_logger().log(level, f"{indent}{message}", *args)


def stage(message: str, *args: object, level: int = logging.INFO) -> None:
    """Write a stage line: one step of the entry, with its rows below it."""
    _write(level, 1, message, *args)


def debug(message: str, *args: object) -> None:
    """Write a working detail at DEBUG, at stage depth: a trace, not a change."""
    _write(logging.DEBUG, 1, message, *args)


def row(
    mark: AuditMark,
    text: str,
    *,
    depth: int = 2,
    level: int = logging.INFO,
) -> None:
    """Write one marked row under the stage above it."""
    _write(level, depth, "%s %s", mark, text)


class AuditEntry:
    """An open entry, closed by `done` or, when the work raises, `FAILED`."""

    def __init__(self, heading: str, args: tuple[object, ...]) -> None:
        """Hold the heading back until a line under it is recorded."""
        self._heading = heading
        self._args = args
        self._indent = INDENT * _depth.get()
        self._level: int | None = None
        self.closed = False

    def announce(self, level: int) -> None:
        """Write the heading, once, at the level of the first line recorded under it."""
        if self._level is not None:
            return
        self._level = level
        get_audit_logger().log(level, f"{self._indent}{self._heading}", *self._args)

    def done(self, message: str, *args: object) -> None:
        """Write the entry's closing line, saying what it did."""
        _write(logging.INFO, 0, f"DONE {message}", *args)
        self.closed = True

    def failed(self, reason: object) -> None:
        """Close the entry as failed, with the reason."""
        _write(logging.ERROR, 0, "FAILED: %s", reason)
        self.closed = True

    def footer(self) -> None:
        """Close the entry with its footer, if its heading was written."""
        if self._level is not None:
            get_audit_logger().log(self._level, f"{self._indent}{FOOTER}")
            get_audit_logger().log(self._level, "")


@contextmanager
def entry(heading: str, *args: object) -> Generator[AuditEntry]:
    """Open an entry: its heading with the first line under it, its footer at the end.

    An entry whose work raises before it closed is closed as `FAILED`, and the
    error goes on. The heading ends with the command that made the change.

    Yields:
        The open entry, for its closing line.
    """
    command = _command.get()
    suffix = f" ({command})" if command else ""
    audit_entry = AuditEntry(f"{heading}{suffix}", args)
    token = _open.set(audit_entry)
    try:
        yield audit_entry
    except BaseException as error:
        if not audit_entry.closed:
            audit_entry.failed(str(error) or type(error).__name__)
        raise
    finally:
        _open.reset(token)
        audit_entry.footer()
