"""Data structures to audit import results.

Provides `ImportResults` which captures the end-to-end flow of an import
operation. This enables richer audit output and reference without having to re-parse
log files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pandas as pd

from domain import SettlementOutcome

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(eq=False)
class MergeEvent:
    """Represents a single merge operation.

    Attributes:
        merged_row: The resulting merged transaction (as Series converted to dict)
        source_rows: Original source transactions that were merged (as DataFrame)
    """

    merged_row: dict[str, Any]
    source_rows: pd.DataFrame


@dataclass
class TransformEvent:
    """Represents a transformation applied to a set of rows.

    Attributes:
        field_name: Name of the field transformed
        old_values: Distinct original values before transform
        new_value: The new value applied
        row_count: Number of rows affected
    """

    field_name: str
    old_values: list[Any]
    new_value: Any
    row_count: int


@dataclass
class CancelEvent:
    """A broker row that cancels an earlier one, and the row it voided.

    Attributes:
        cancellation: The cancelling row, as read.
        cancelled: The row it voided, or None when the file held no row for it
            (the voided row may already be stored from an earlier import).
    """

    cancellation: dict[str, Any]
    cancelled: dict[str, Any] | None = None

    def rows(self) -> int:
        """Return how many rows the cancellation took out of the import."""
        return 1 if self.cancelled is None else 2


@dataclass(eq=False)
class ImportResults:
    """Results for a transaction import operation.

    DataFrames are captured at key pipeline stages for tracking of import
    operations. Summary metrics and events are also recorded.
    """

    read_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    invalid_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    intra_rejected_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    db_rejected_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    final_df: pd.DataFrame = field(default_factory=pd.DataFrame)

    merge_events: list[MergeEvent] = field(default_factory=list)
    transform_events: list[TransformEvent] = field(default_factory=list)
    cancel_events: list[CancelEvent] = field(default_factory=list)

    # Database counts
    final_db_count: int = 0

    def imported_count(self) -> int:
        """Return number of transactions imported."""
        return len(self.final_df)

    def read_count(self) -> int:
        """Return number of source rows read from source file."""
        return len(self.read_df)

    def invalid_count(self) -> int:
        """Return number of invalid rows rejected by formatter validation."""
        return len(self.invalid_df)

    def intra_rejected_count(self) -> int:
        """Return number of intra-import duplicates rejected."""
        return len(self.intra_rejected_df)

    def db_rejected_count(self) -> int:
        """Return number of database duplicates rejected."""
        return len(self.db_rejected_df)

    def merge_candidates(self) -> int:
        """Return number of transactions that were merge candidates."""
        return sum(len(me.source_rows) for me in self.merge_events)

    def merged_into(self) -> int:
        """Return number of merge operations performed."""
        return len(self.merge_events)

    def cancelled_count(self) -> int:
        """Return how many rows cancellations took out, cancelling rows included."""
        return sum(event.rows() for event in self.cancel_events)

    def expected_count(self) -> int:
        """Return how many rows should have been imported, by tallying each stage.

        Rows read, less cancelled rows, less the net shrink from merges, less
        invalid rows and both kinds of rejected duplicate. Anything other than
        `imported_count` means rows went missing (or appeared) somewhere the
        audit does not account for.
        """
        merge_delta = self.merged_into() - self.merge_candidates()
        return (
            self.read_count()
            - self.cancelled_count()
            + merge_delta
            - self.invalid_count()
            - self.intra_rejected_count()
            - self.db_rejected_count()
        )

    def tally_matches(self) -> bool:
        """Return whether the imported rows match the stage-by-stage tally."""
        return self.imported_count() == self.expected_count()


@dataclass
class SettlementMatch:
    """One statement row weighed against the folio's calculated settlement dates.

    Every candidate row a statement offers produces one of these, matched or
    not, so an import can report what it did and what it could not place.

    Attributes:
        outcome: Whether the row matched exactly one transaction, none, or several.
        settle_date: The settlement date the statement reports for the row.
        txn_date: Trade date parsed out of the statement description.
        action: Statement action code (BUY/SELL/...).
        ticker: Ticker after normalization and the user's transform rules.
        currency: Currency the row is denominated in.
        amount: Absolute amount used for matching.
        units: Units parsed from the description, when the row carries one.
        account: Account the statement belongs to, from its filename.
        candidates: How many folio transactions the row matched.
        txn_id: The transaction updated, set only when the outcome is MATCHED.
    """

    outcome: SettlementOutcome
    settle_date: str
    txn_date: str
    action: str
    ticker: str
    currency: str
    amount: float
    units: float | None = None
    account: str | None = None
    candidates: int = 0
    txn_id: int | None = None


@dataclass
class StatementImportResult:
    """Result for a statement import operation."""

    settlement_updates: int = 0
    transfer_results: ImportResults | None = None
    transfers_rejected: int = 0
    transfers_skipped: int = 0
    settlement_matches: list[SettlementMatch] = field(default_factory=list)

    def transfers_created(self) -> int:
        """Return number of transfer transactions created from this statement."""
        return self.transfer_results.imported_count() if self.transfer_results else 0

    def settlement_candidates(self) -> int:
        """Return how many statement rows were weighed for a settlement date."""
        return len(self.settlement_matches)

    def settlement_already_settled(self) -> int:
        """Return how many rows were already settled."""
        return sum(
            match.outcome is SettlementOutcome.ALREADY_SETTLED
            for match in self.settlement_matches
        )

    def settlement_unplaced(self) -> list[SettlementMatch]:
        """Return the candidate rows that could not be placed on a transaction."""
        settled = (SettlementOutcome.MATCHED, SettlementOutcome.ALREADY_SETTLED)
        return [
            match for match in self.settlement_matches if match.outcome not in settled
        ]


@dataclass(frozen=True)
class FileImport:
    """Single file import's details.

    Attributes:
        path: The file, where it was when the import ran.
        results: The import's audit, or None when the import failed.
        error: Why the import failed, or None when it succeeded.
    """

    path: Path
    results: ImportResults | None = None
    error: str | None = None

    @property
    def imported(self) -> int:
        """Return how many transactions the file added."""
        return self.results.imported_count() if self.results else 0
