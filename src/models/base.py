"""Base model class for API response serialization and deserialization."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import TypeVar

import dateutil.parser

T = TypeVar("T")


def from_str(x: str | None) -> str | None:
    """Validate and return a string value or None."""
    if x is not None and not isinstance(x, str):
        msg = f"Expected str or None, got {type(x).__name__}"
        raise TypeError(msg)
    return x


def from_str_strict(x: str | None) -> str:
    """Validate and return a string value or raise an error."""
    if x is None:
        msg = "Expected str, got None"
        raise TypeError(msg)
    if not isinstance(x, str):
        msg = f"Expected str, got {type(x).__name__}"
        raise TypeError(msg)
    return x


def from_bool(x: object) -> bool:
    """Validate and return a boolean value."""
    if not isinstance(x, bool):
        msg = f"Expected bool, got {type(x).__name__}"
        raise TypeError(msg)
    return x


def from_bool_optional(x: object | None) -> bool | None:
    """Validate and return a boolean value or None."""
    if x is None:
        return None
    if not isinstance(x, bool):
        msg = f"Expected bool or None, got {type(x).__name__}"
        raise TypeError(msg)
    return x


def from_int(x: object) -> int:
    """Validate and return an integer value."""
    if not isinstance(x, int) or isinstance(x, bool):
        msg = f"Expected int, got {type(x).__name__}"
        raise TypeError(msg)
    return x


def from_list[T](f: Callable[[object], T], x: object) -> list[T]:
    """Convert a list by applying a function to each element."""
    if not isinstance(x, list):
        msg = f"Expected list, got {type(x).__name__}"
        raise TypeError(msg)
    return [f(y) for y in x]


def to_enum[T](c: type[T], x: object) -> str:
    """Convert an enum value to its string representation."""
    if not isinstance(x, c):
        msg = f"Expected instance of {c.__name__}, got {type(x).__name__}"
        raise TypeError(msg)
    return x.value  # type: ignore[union-attr]


def to_class[T](c: type[T], x: object) -> dict:
    """Convert an object to its dictionary representation."""
    if not isinstance(x, c):
        msg = f"Expected instance of {c.__name__}, got {type(x).__name__}"
        raise TypeError(msg)
    return x.to_dict()  # type: ignore[attr-defined]


def from_datetime(x: str) -> datetime:
    """Parse and return a datetime value from a string."""
    return dateutil.parser.parse(x)


def from_datetime_optional(x: str | None) -> datetime | None:
    """Parse optional datetime value from a string or None."""
    return from_datetime(x) if x is not None else None


def parse_obj[T](
    deserializer: Callable[[dict], T],
    obj: dict | None,
    key: str | None = None,
    class_name: str = "",
) -> T:
    """Extract and deserialize a required nested object.

    Args:
        deserializer: Function to deserialize the dict (e.g., Money.from_dict).
        obj: Parent dict or the dict to deserialize directly.
        key: Key to extract from parent dict. If None, obj is deserialized directly.
        class_name: Parent class name for error messages.

    Returns:
        Deserialized object.

    Raises:
        ValueError: If required key is missing.
        TypeError: If value is not a dict.
    """
    # If key is provided, extract from parent dict
    if key is not None:
        value_dict = obj.get(key) if obj else None
        if value_dict is None:
            msg = f"Missing '{key}' field in {class_name} dictionary"
            raise ValueError(msg)
    else:
        # Otherwise deserialize obj directly
        value_dict = obj

    if not isinstance(value_dict, dict):
        msg = f"Expected dict, got {type(value_dict).__name__}"
        raise TypeError(msg)

    return deserializer(value_dict)


def parse_obj_optional[T](
    deserializer: Callable[[dict], T],
    obj: dict | None,
) -> T | None:
    """Extract and deserialize an optional nested object.

    Args:
        deserializer: Function to deserialize the dict.
        obj: Dict to deserialize or None.

    Returns:
        Deserialized object or None.
    """
    return deserializer(obj) if obj is not None else None


class SerializableModel(ABC):
    """Abstract base class that can be automatically serialized to a dictionary."""

    @staticmethod
    @abstractmethod
    def from_dict(obj: dict) -> "SerializableModel":
        """Create instance from dictionary.

        Args:
            obj: Dictionary containing model data.

        Returns:
            Model instance.
        """
        if not isinstance(obj, dict):
            msg = f"Expected dict, got {type(obj).__name__}"
            raise TypeError(msg)

    def to_dict(self) -> dict:
        """Auto-generate dict from dataclass fields.

        Returns:
            Dictionary representation of the model.

        Raises:
            TypeError: If instance is not a dataclass.
        """
        if not is_dataclass(self):
            msg = f"{self.__class__.__name__} must be a dataclass"
            raise TypeError(msg)

        result: dict = {}
        for field in fields(self):
            value = getattr(self, field.name)

            if value is None:
                result[self._get_api_field_name(field.name)] = None
            elif isinstance(value, list):
                result[self._get_api_field_name(field.name)] = [
                    item.to_dict()
                    if hasattr(item, "to_dict")
                    else (item.value if isinstance(item, Enum) else item)
                    for item in value
                ]
            elif hasattr(value, "to_dict"):
                result[self._get_api_field_name(field.name)] = value.to_dict()
            elif isinstance(value, Enum):
                result[self._get_api_field_name(field.name)] = value.value
            elif isinstance(value, datetime):
                result[self._get_api_field_name(field.name)] = value.isoformat()
            else:
                result[self._get_api_field_name(field.name)] = value

        return result

    @staticmethod
    def _get_api_field_name(python_field_name: str) -> str:
        """Convert Python snake_case to API camelCase.

        Args:
            python_field_name: Python field name in snake_case.

        Returns:
            API field name in camelCase or special format.
        """
        if python_field_name == "typename":
            return "__typename"
        if python_field_name == "id":
            return "id"

        parts = python_field_name.split("_")
        return parts[0] + "".join(word.capitalize() for word in parts[1:])
