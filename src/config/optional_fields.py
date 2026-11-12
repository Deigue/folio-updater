"""Schema for optional fields configuration."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping


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


class OptionalFieldsConfig:
    """Configuration manager of optional fields."""

    def __init__(self, config_dict: dict[str, dict] | None = None) -> None:
        """Initialize with optional fields configuration.

        Args:
            config_dict: Dictionary mapping resolved column names to config dicts
        """
        self._fields: dict[str, OptionalField] = {}
        if config_dict:
            for name, entry in config_dict.items():
                self._fields[name] = OptionalField.from_config_entry(name, entry)

    def get_all_fields(self) -> dict[str, OptionalField]:
        """Get all configured optional fields.

        Returns:
            Dictionary mapping resolved column names to OptionalField instances
        """
        return self._fields.copy()

    def columns_of_type(
        self,
        field_type: FieldType,
        live_columns: Collection[str],
    ) -> list[str]:
        """List the configured columns of one type that a table really has.

        Args:
            field_type: The type to keep.
            live_columns: The columns the table currently holds.

        Returns:
            Their names, in the order they were configured.
        """
        return [
            name
            for name, field in self._fields.items()
            if field.field_type == field_type and name in live_columns
        ]

    def __len__(self) -> int:
        """Return number of configured optional fields."""
        return len(self._fields)
