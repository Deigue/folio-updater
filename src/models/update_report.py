"""Models for what `folio update` run does.

Each stage of the run prints its own detailed output as it goes. This report is
the condensed account read at the end: the rows the run added or changed, the
concerns worth a second look (each with the command that resolves it), and
what is deliberately left for future runs.
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


@dataclass(frozen=True)
class Concern:
    """One thing the run did that a human should confirm or correct.

    Attributes:
        stage: The stage that raised it.
        title: Short heading, e.g. "Excluded rows in ibkr_Trades.csv".
        why: What it means and why it matters, in a sentence or two.
        rows: The offending rows, if the concern is about specific rows.
        commands: Follow-up commands that resolve it, ready to copy.
    """

    stage: UpdateStage
    title: str
    why: str
    rows: pd.DataFrame = field(default_factory=pd.DataFrame)
    commands: tuple[str, ...] = ()


@dataclass(frozen=True)
class HardFailure:
    """A failure that stopped the run before it could finish.

    Attributes:
        stage: The stage that failed.
        error: What went wrong, as reported.
        next_steps: How to get back to where a clean run would have ended.
    """

    stage: UpdateStage
    error: str
    next_steps: tuple[str, ...] = ()


@dataclass
class UpdateReport:
    """Everything a `folio update` run reports at the end.

    Attributes:
        new_txns: Transactions added by this run, as stored.
        settled: Statement rows that turned a calculated settlement date into
            a confirmed one.
        workbook: The workbook written, or None when generation did not run.
        concerns: What needs a human, in the order it was found.
        pending: Lines describing what is expected to resolve on a later run.
        failure: Set when a stage stopped the run.
    """

    new_txns: pd.DataFrame = field(default_factory=pd.DataFrame)
    settled: list[SettlementMatch] = field(default_factory=list)
    workbook: Path | None = None
    concerns: list[Concern] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    failure: HardFailure | None = None

    @property
    def failed(self) -> bool:
        """Return whether a stage stopped the run."""
        return self.failure is not None
