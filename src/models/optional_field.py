"""An optional source column the user configures, and the type it is formatted as."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


class FieldType(Enum):
    """Supported optional field data types."""

    DATE = "date"
    NUMERIC = "numeric"
    CURRENCY = "currency"
    ACTION = "action"
    STRING = "string"


class OptionalField:
    """Represents a single optional field configuration."""

    def __init__(self, name: str, keywords: list[str], field_type: FieldType) -> None:
        """Initialize an OptionalField.

        Args:
            name: The resolved column name.
            keywords: List of header names to match.
            field_type: The data type for formatting.
        """
        self.name = name
        self.keywords = [k.lower() for k in keywords]
        self.field_type = field_type

    @classmethod
    def from_config_entry(cls, name: str, entry: Mapping[str, Any]) -> OptionalField:
        """Create an OptionalField from a validated `optional_columns` entry.

        Args:
            name: The resolved column name.
            entry: Its `keywords` (default the column's own name) and `type`
                (default `string`), as validation left them.

        Returns:
            OptionalField instance
        """
        return cls(
            name=name,
            keywords=entry.get("keywords", [name]),
            field_type=FieldType(entry.get("type", FieldType.STRING.value)),
        )
