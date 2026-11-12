"""Base model class and field validators for reading API responses."""

import traceback
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from enum import Enum


def get_last_3_frames() -> str:
    """Get the last 3 stack frames excluding the current frame.

    Returns:
        Formatted string of the last 3 method calls in the stack.
    """
    stack_lines = traceback.format_stack()[:-1]  # Exclude current frame
    # Keep only the last 3 method calls
    stack_lines = stack_lines[-3:]
    return "".join(stack_lines)


def from_str(x: str | None) -> str | None:
    """Validate and return a string value or None."""
    if x is not None and not isinstance(x, str):
        msg = (
            f"Expected str or None, got {type(x).__name__}\n"
            f"Call stack:\n{get_last_3_frames()}"
        )
        raise TypeError(msg)
    return x


def from_str_strict(x: str | None) -> str:
    """Validate and return a string value or raise an error."""
    if x is None:
        msg = f"Expected str, got None\nCall stack:\n{get_last_3_frames()}"
        raise TypeError(msg)
    if not isinstance(x, str):
        msg = (
            f"Expected str, got {type(x).__name__}\nCall stack:\n{get_last_3_frames()}"
        )
        raise TypeError(msg)
    return x


def from_bool_optional(x: object | None) -> bool | None:
    """Validate and return a boolean value or None."""
    if x is None:
        return None
    if not isinstance(x, bool):
        msg = (
            f"Expected bool or None, got {type(x).__name__}\n"
            f"Call stack:\n{get_last_3_frames()}"
        )
        raise TypeError(msg)
    return x


def from_int(x: object) -> int:
    """Validate and return an integer value."""
    if not isinstance(x, int) or isinstance(x, bool):
        msg = (
            f"Expected int, got {type(x).__name__}\nCall stack:\n{get_last_3_frames()}"
        )
        raise TypeError(msg)
    return x


def from_enum[T: Enum](enum_class: type[T], value: object) -> T:
    """Validate and return an enum value from any hashable type.

    Args:
        enum_class: The enum class to validate against.
        value: The value to convert to enum (must be a valid enum value).

    Returns:
        The enum member.

    Raises:
        ValueError: If value is not a valid enum member.
    """
    try:
        return enum_class(value)
    except (ValueError, KeyError) as e:
        valid_values = [member.value for member in enum_class]
        msg = (
            f"Expected {enum_class.__name__}, got {value!r}\n"
            f"Valid values: {valid_values}\n"
            f"Call stack:\n{get_last_3_frames()}"
        )
        raise ValueError(msg) from e


def from_list[T](f: Callable[[object], T], x: object) -> list[T]:
    """Convert a list by applying a function to each element."""
    if not isinstance(x, list):
        msg = (
            f"Expected list, got {type(x).__name__}\nCall stack:\n{get_last_3_frames()}"
        )
        raise TypeError(msg)
    return [f(y) for y in x]


def from_datetime(x: str) -> datetime:
    """Parse an ISO 8601 date or timestamp, raising on anything else."""
    try:
        return datetime.fromisoformat(x)
    except (ValueError, TypeError) as e:
        msg = f"Failed to parse datetime: {x!r}\nCall stack:\n{get_last_3_frames()}"
        raise ValueError(msg) from e


def from_datetime_required(obj: dict, key: str, owner: str) -> datetime:
    """Parse a required date field, naming the field and its owner when absent.

    Args:
        obj: The payload dict holding the field.
        key: The field's key in the payload.
        owner: The model being built, for the error message.

    Returns:
        The parsed datetime.

    Raises:
        ValueError: If the field is missing or null, or is not an ISO 8601 date.
    """
    raw = obj.get(key)
    if raw is None:
        msg = (
            f"Missing '{key}' field in {owner} dictionary\n"
            f"Call stack:\n{get_last_3_frames()}"
        )
        raise ValueError(msg)
    return from_datetime(raw)


def from_datetime_optional(x: str | None) -> datetime | None:
    """Parse optional datetime value from a string or None."""
    return from_datetime(x) if x is not None else None


def parse_obj[T](deserializer: Callable[[dict], T], obj: object) -> T:
    """Deserialize a required nested object, raising TypeError if it is not a dict."""
    if not isinstance(obj, dict):
        msg = (
            f"Expected dict, got {type(obj).__name__}\n"
            f"Call stack:\n{get_last_3_frames()}"
        )
        raise TypeError(msg)
    return deserializer(obj)


def parse_obj_optional[T](deserializer: Callable[[dict], T], obj: object) -> T | None:
    """Deserialize an optional nested object: None stays None, a dict is parsed."""
    return None if obj is None else parse_obj(deserializer, obj)


class SerializableModel(ABC):
    """Abstract base class for a model built from an API response dictionary."""

    @staticmethod
    @abstractmethod
    def from_dict(obj: dict) -> "SerializableModel":
        """Create instance from dictionary.

        Args:
            obj: Dictionary containing model data.

        Returns:
            Model instance.
        """
