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
    """Represents a single optional field configuration."""

    name: str
    keywords: list[str]
    field_type: FieldType

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
    def from_config_entry(cls, name: str, entry: dict) -> OptionalField:
        """Create an OptionalField from config entry.

        Args:
            name: Field name
            entry: Dict with 'keywords' and 'type'
        Returns:
            OptionalField instance
        Raises:
            ValueError: If type_str is not a valid FieldType
        """
        keywords = entry.get("keywords", [name])
        type_str = entry.get("type", "string")
        try:
            field_type = FieldType(type_str.lower())
        except ValueError as e:  # pragma: no cover
            valid_types = [ft.value for ft in FieldType]
            msg = (
                f"Invalid field type '{type_str}' for field '{name}'. "
                f"Valid types: {valid_types}"
            )
            raise ValueError(msg) from e
        return cls(name=name, keywords=keywords, field_type=field_type)


class OptionalFieldsConfig:
    """Configuration manager of optional fields."""

    def __init__(self, config_dict: dict[str, dict] | None = None) -> None:
        """Initialize with optional fields configuration.

        Args:
            config_dict: Dictionary mapping resolved column names to config dicts
        """
        self._fields: dict[str, OptionalField] = {}
        self._keyword_map: dict[str, str] = {}
        if config_dict:
            for name, entry in config_dict.items():
                field = OptionalField.from_config_entry(name, entry)
                self._fields[name] = field
                for kw in field.keywords:
                    self._keyword_map[kw] = name

    def resolve_column(self, header: str) -> str | None:  # pragma: no cover
        """Resolve a header name to the logical column name.

        Args:
            header: The header name to resolve
        Returns:
            The resolved column name or None
        """
        return self._keyword_map.get(header.lower())

    def get_field(self, name: str) -> OptionalField | None:
        """Get optional field by resolved column name.

        Args:
            name: The resolved column name
        Returns:
            OptionalField if found, None otherwise
        """
        return self._fields.get(name)

    def get_field_by_header(
        self,
        header: str,
    ) -> OptionalField | None:  # pragma: no cover
        """Get optional field by header name.

        Args:
            header: The header name to resolve
        Returns:
            OptionalField if found, None otherwise
        """
        col = self.resolve_column(header)
        if col:
            return self.get_field(col)
        return None

    def get_all_fields(self) -> dict[str, OptionalField]:  # pragma: no cover
        """Get all configured optional fields.

        Returns:
            Dictionary mapping resolved column names to OptionalField instances
        """
        return self._fields.copy()

    def has_field(self, name: str) -> bool:  # pragma: no cover
        """Check if a field is configured as optional.

        Args:
            name: The resolved column name
        Returns:
            True if field is configured, False otherwise
        """
        return name in self._fields

    def __len__(self) -> int:  # pragma: no cover
        """Return number of configured optional fields."""
        return len(self._fields)

    def __bool__(self) -> bool:
        """Return True if any optional fields are configured."""
        return bool(self._fields)
