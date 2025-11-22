"""Data structures to audit import results.

Provides `ImportResults` which captures the end-to-end flow of an import
operation. This enables richer audit output and reference without having to re-parse
log files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


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

    def flow_summary(self) -> dict[str, Any]:
        """Compute high level summary of import flow.

        Returns:
            Dictionary summarizing flow counts for each stage.
        """
        return {
            "Read": self.read_count(),
            "Merge Candidates": self.merge_candidates,
            "Merged Into": self.merged_into,
            "Excluded (format)": self.excluded_count(),
            "Intra Duplicates Rejected": self.intra_rejected_count(),
            "DB Duplicates Rejected": self.db_rejected_count(),
            "Imported": self.imported_count(),
        }

    def __int__(self) -> int:  # backwards compatibility if cast to int
        """Return imported count when cast to int (for backwards compatibility)."""
        return self.imported_count()
