"""Say something to the user and write it to the log in one call.

Import the module, not its functions: `announce.success(...)` reads as what it
does, and stays distinct from `console_success(...)`, which only prints.
"""

from __future__ import annotations

import logging
from enum import Enum

from term.console import (
    console_error,
    console_info,
    console_print,
    console_success,
    console_warning,
)


class LogLevel(Enum):
    """Log level enumeration for unified logging."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    SUCCESS = "success"


# Mapping of log levels to logger methods
_LOGGER_METHODS = {
    LogLevel.DEBUG: "debug",
    LogLevel.INFO: "info",
    LogLevel.SUCCESS: "info",
    LogLevel.WARNING: "warning",
    LogLevel.ERROR: "error",
    LogLevel.CRITICAL: "critical",
}

# Mapping of log levels to console functions
_CONSOLE_FUNCTIONS = {
    LogLevel.DEBUG: lambda msg: console_print(msg, "dim"),
    LogLevel.INFO: console_info,
    LogLevel.SUCCESS: console_success,
    LogLevel.WARNING: console_warning,
    LogLevel.ERROR: console_error,
    LogLevel.CRITICAL: console_error,
}


def at_level(
    message: str,
    level: LogLevel = LogLevel.INFO,
    logger_name: str | None = None,
    stacklevel: int = 2,
) -> None:
    """Log a message to file and display it on the console.

    Args:
        message: Message to log and display
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL, SUCCESS)
        logger_name: Logger name to use (None for root logger)
        stacklevel: How many frames up the stack to look for the caller (default 2)

    Example:
        announce.at_level("Import completed", LogLevel.SUCCESS, "importer")
        announce.at_level("Processing file...", LogLevel.INFO)
    """
    logger = logging.getLogger(logger_name) if logger_name else logging.getLogger()

    log_method = _LOGGER_METHODS.get(level, "info")
    getattr(logger, log_method)(message, stacklevel=stacklevel)

    console_func = _CONSOLE_FUNCTIONS.get(level, console_print)
    console_func(message)


def debug(message: str, logger_name: str | None = None) -> None:
    """Log debug message to both file and console.

    Args:
        message: Debug message
        logger_name: Logger name (None for root logger)
    """
    at_level(message, LogLevel.DEBUG, logger_name, stacklevel=3)


def info(message: str, logger_name: str | None = None) -> None:
    """Log info message to both file and console.

    Args:
        message: Info message
        logger_name: Logger name (None for root logger)
    """
    at_level(message, LogLevel.INFO, logger_name, stacklevel=3)


def warning(message: str, logger_name: str | None = None) -> None:
    """Log warning message to both file and console.

    Args:
        message: Warning message
        logger_name: Logger name (None for root logger)
    """
    at_level(message, LogLevel.WARNING, logger_name, stacklevel=3)


def error(message: str, logger_name: str | None = None) -> None:
    """Log error message to both file and console.

    Args:
        message: Error message
        logger_name: Logger name (None for root logger)
    """
    at_level(message, LogLevel.ERROR, logger_name, stacklevel=3)


def critical(message: str, logger_name: str | None = None) -> None:
    """Log critical message to both file and console.

    Args:
        message: Critical message
        logger_name: Logger name (None for root logger)
    """
    at_level(message, LogLevel.CRITICAL, logger_name, stacklevel=3)


def success(message: str, logger_name: str | None = None) -> None:
    """Log success message to both file and console.

    Args:
        message: Success message
        logger_name: Logger name (None for root logger)
    """
    at_level(message, LogLevel.SUCCESS, logger_name, stacklevel=3)
