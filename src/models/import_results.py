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
        source_rows: Original source transactions merged (DataFrame limited copy)
    """

    merged_row: dict[str, Any]
    source_rows: pd.DataFrame

