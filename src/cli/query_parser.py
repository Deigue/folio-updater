"""Smart query parser for the folio CLI."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from db import get_connection, get_distinct_values
from utils import TORONTO_TZ
from utils.constants import Action, Column, Table

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass
class QueryFilter:
    """Represents a single filter in a query."""

    column: str
    operator: str  # ":", "~", ">", "<", ">=", "<="
    value: str

    def __repr__(self) -> str:
        """Return string representation of the filter."""
        if self.operator == ":":
            return f'{self.column}="{self.value}"'
        return f"{self.column}{self.operator}{self.value}"


@dataclass
class QuerySort:
    """Represents a sort specification in a query."""

    column: str
    direction: str  # "asc" or "desc"

    def __repr__(self) -> str:
        """Return string representation of the sort."""
        direction_str = "DESC" if self.direction == "desc" else "ASC"
        return f"sort:{self.column}({direction_str})"


@dataclass
class ParsedQuery:
    """Represents a fully parsed query."""

    filters: list[QueryFilter] = field(default_factory=list)
    text_searches: list[str] = field(default_factory=list)  # General text search terms
    sorts: list[QuerySort] = field(default_factory=list)

    def __repr__(self) -> str:
        """Return string representation of the parsed query."""
        query: list[str] = [repr(f) for f in self.filters]
        query.extend(f'text~"{search}"' for search in self.text_searches)
        query.extend(repr(s) for s in self.sorts)
        if not self.filters and not self.text_searches and not self.sorts:
            return "Querying all transactions (no filters applied)."
        return "Filters: " + ", ".join(query)

