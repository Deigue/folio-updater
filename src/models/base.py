"""Base model class for API response serialization and deserialization."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime

import dateutil.parser


def from_str(x: str | None) -> str | None:
    """Validate and return a string value or None.

    Args:
        x: The value to validate as a string or None.

    Returns:
        The input string value or None.

    Raises:
        TypeError: If x is not a string or None.
    """
    if x is not None and not isinstance(x, str):
        msg = f"Expected str or None, got {type(x).__name__}"
        raise TypeError(msg)
    return x


def from_str_strict(x: str | None) -> str:
    """Validate and return a string value or raise an error.

    Args:
        x: The value to validate as a string or None.

    Returns:
        The input string value.

    Raises:
        TypeError: If x is not a string.
    """
    if x is None:
        msg = "Expected str, got None"
        raise TypeError(msg)
    if not isinstance(x, str):
        msg = f"Expected str, got {type(x).__name__}"
        raise TypeError(msg)
    return x


def from_bool(x: object) -> bool:
    """Validate and return a boolean value.

    Args:
        x: The value to validate as a boolean.

    Returns:
        The boolean value.

    Raises:
        TypeError: If x is not a boolean.
    """
    if not isinstance(x, bool):
        msg = f"Expected bool, got {type(x).__name__}"
        raise TypeError(msg)
    return x


def from_int(x: object) -> int:
    """Validate and return an integer value.

    Args:
        x: The value to validate as an integer.

    Returns:
        The integer value.

    Raises:
        TypeError: If x is not an integer or is a boolean.
    """
    if not isinstance(x, int) or isinstance(x, bool):
        msg = f"Expected int, got {type(x).__name__}"
        raise TypeError(msg)
    return x


def from_list[T](f: Callable[[object], T], x: object) -> list[T]:
    """Convert a list by applying a function to each element.

    Args:
        f: The function to apply to each element.
        x: The list to convert.

    Returns:
        List with function applied to each element.

    Raises:
        TypeError: If x is not a list.
    """
    if not isinstance(x, list):
        msg = f"Expected list, got {type(x).__name__}"
        raise TypeError(msg)
    return [f(y) for y in x]


def to_enum[T](c: type[T], x: object) -> str:
    """Convert an enum value to its string representation.

    Args:
        c: The enum class type.
        x: The enum instance to convert.

    Returns:
        String value of the enum.

    Raises:
        TypeError: If x is not an instance of c.
    """
    if not isinstance(x, c):
        msg = f"Expected instance of {c.__name__}, got {type(x).__name__}"
        raise TypeError(msg)
    return x.value  # type: ignore[union-attr]


def to_class[T](c: type[T], x: object) -> dict:
    """Convert an object to its dictionary representation.

    Args:
        c: The class type of the object.
        x: The object instance to convert.

    Returns:
        Dictionary representation of the object.

    Raises:
        TypeError: If x is not an instance of c.
    """
    if not isinstance(x, c):
        msg = f"Expected instance of {c.__name__}, got {type(x).__name__}"
        raise TypeError(msg)
    return x.to_dict()  # type: ignore[attr-defined]


def from_datetime(x: str) -> datetime:
    """Parse and return a datetime value from a string.

    Args:
        x: ISO format datetime string to parse.

    Returns:
        Parsed datetime object.
    """
    return dateutil.parser.parse(x)


class BaseModel(ABC):
    """Abstract base class for API models with serialization support."""

    @staticmethod
    @abstractmethod
    def from_dict(obj: dict) -> "BaseModel":
        """Create a model instance from a dictionary.

        Args:
            obj: Dictionary containing model data, typically from an API response.

        Returns:
            Model instance populated with data from the dictionary.

        Raises:
            TypeError: If obj is not a dictionary.
        """
        if not isinstance(obj, dict):
            msg = f"Expected dict, got {type(obj).__name__}"
            raise TypeError(msg)

    @abstractmethod
    def to_dict(self) -> dict:
        """Convert the model instance to a dictionary representation.

        Returns:
            Dictionary representation of the model instance.
        """
