"""Models for what a `folio update` does.

Each stage of the update prints its own detailed output as it goes. This report is
the condensed account read at the end: the rows the update added or changed, the
concerns worth a second look (each with the command that resolves it), what a
later update will resolve on its own, and what was expected and needs nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from pathlib import Path

    from models.import_results import SettlementMatch


class UpdateStage(StrEnum):
    """The stages of an update, in the order they run."""

    DOWNLOAD = "Download"
    IMPORT = "Import"
    STATEMENTS = "Statements"
    CHECK = "Check"
    GENERATE = "Generate"


@dataclass(frozen=True, eq=False)
class Concern:
    """One thing the update did that a human should confirm or correct.

    Attributes:
        stage: The stage that raised it.
        title: Short heading, e.g. "Invalid rows in ibkr_Trades.csv".
        why: What it means and why it matters, in a sentence or two.
        rows: The offending rows, if the concern is about specific rows.
        details: Free-text findings, one per line, when there are no rows.
        commands: Follow-up commands that resolve it, ready to copy.
    """

    stage: UpdateStage
    title: str
    why: str
    rows: pd.DataFrame = field(default_factory=pd.DataFrame)
    details: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()


@dataclass(frozen=True)
class HardFailure:
    """A failure that stopped the update before it could finish.

    Attributes:
        stage: The stage that failed.
        error: What went wrong, as reported.
        next_steps: How to get back to where a clean update would have ended.
    """

    stage: UpdateStage
    error: str
    next_steps: tuple[str, ...] = ()


@dataclass(eq=False)
class UpdateReport:
    """Everything a `folio update` reports at the end.

    Attributes:
        new_txns: Transactions added by this update, as stored.
        confirmed: Statement rows that turned a calculated settlement date into
            a confirmed one.
        workbook: The workbook written, or None when generation did not run.
        concerns: What needs a human, in the order it was found.
        pending: Lines describing what a later update is expected to resolve
            on its own, such as settlement dates waiting on a statement.
        expected: Lines noting what the update met as expected that needs nothing,
            now or later, such as the overlap day's rows arriving again.
        failure: Set when a stage stopped the update.
    """

    new_txns: pd.DataFrame = field(default_factory=pd.DataFrame)
    confirmed: list[SettlementMatch] = field(default_factory=list)
    workbook: Path | None = None
    concerns: list[Concern] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    expected: list[str] = field(default_factory=list)
    failure: HardFailure | None = None

    @property
    def failed(self) -> bool:
        """Return whether a stage stopped the update."""
        return self.failure is not None
