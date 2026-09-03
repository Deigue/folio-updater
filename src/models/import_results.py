"""Data structures to audit import results.

Provides `ImportResults` which captures the end-to-end flow of an import
operation. This enables richer audit output and reference without having to re-parse
log files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from domain import SettlementOutcome


@dataclass
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
class ImportResults:
    """Results for a transaction import operation.

    DataFrames are captured at key pipeline stages for atomic tracking of import
    operations. Summary metrics and events are also recorded.
    """

    read_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    mapped_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    transformed_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    excluded_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    intra_approved_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    intra_rejected_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    db_approved_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    db_rejected_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    final_df: pd.DataFrame = field(default_factory=pd.DataFrame)

    merge_events: list[MergeEvent] = field(default_factory=list)
    transform_events: list[TransformEvent] = field(default_factory=list)

    # Database counts
    existing_count: int = 0
    final_db_count: int = 0

    def imported_count(self) -> int:
        """Return number of transactions imported."""
        return len(self.final_df)

    def read_count(self) -> int:
        """Return number of raw transactions read from source file."""
        return len(self.read_df)

    def excluded_count(self) -> int:
        """Return number of transactions excluded by formatter validation."""
        return len(self.excluded_df)

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

    def __int__(self) -> int:  # backwards compatibility if cast to int
        """Return imported count when cast to int (for backwards compatibility)."""
        return self.imported_count()


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
        units: Share count parsed from the description, when the row carries one.
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
