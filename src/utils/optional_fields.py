"""Schema for optional fields configuration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FieldType(Enum):
    """Supported optional field data types."""

    # Existing data types we already use
    DATE = "date"
    NUMERIC = "numeric"
    CURRENCY = "currency"
    ACTION = "action"
    STRING = "string"


@dataclass
class OptionalField:
    """Represents a single optional field configuration.

    Args:
        name: The field name as it appears in the configuration
        field_type: The data type to apply formatting
    """

    name: str
    field_type: FieldType

    @classmethod
    def from_config_entry(cls, name: str, type_str: str) -> OptionalField:
        """Create an OptionalField from config entry.

        Args:
            name: Field name
            type_str: Type string from config (e.g., "date", "numeric")

        Returns:
            OptionalField instance

        Raises:
            ValueError: If type_str is not a valid FieldType
        """
        try:
            field_type = FieldType(type_str.lower())
        except ValueError as e:
            valid_types = [ft.value for ft in FieldType]
            msg = (
                f"Invalid field type '{type_str}' for field '{name}'. "
                f"Valid types: {valid_types}"
            )
            raise ValueError(msg) from e

        return cls(name=name, field_type=field_type)


class OptionalFieldsConfig:
    """Configuration manager for optional fields."""

    def __init__(self, config_dict: dict[str, str] | None = None) -> None:
        """Initialize with optional fields configuration.

        Args:
            config_dict: Dictionary mapping field names to type strings
        """
        self._fields: dict[str, OptionalField] = {}

        if config_dict:
            for name, type_str in config_dict.items():
                field = OptionalField.from_config_entry(name, type_str)
                self._fields[name] = field

    def get_field(self, name: str) -> OptionalField | None:
        """Get optional field by name.

        Args:
            name: Field name to look up

        Returns:
            OptionalField if found, None otherwise
        """
        return self._fields.get(name)

    def get_all_fields(self) -> dict[str, OptionalField]:
        """Get all configured optional fields.

        Returns:
            Dictionary mapping field names to OptionalField instances
        """
        return self._fields.copy()

    def has_field(self, name: str) -> bool:
        """Check if a field is configured as optional.

        Args:
            name: Field name to check

        Returns:
            True if field is configured, False otherwise
        """
        return name in self._fields

    def __len__(self) -> int:
        """Return number of configured optional fields."""
        return len(self._fields)

    def __bool__(self) -> bool:
        """Return True if any optional fields are configured."""
        return bool(self._fields)
