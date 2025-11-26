"""Unified logging and console output utilities.

This module provides helper functions that log messages to both the file logger
and the Rich console simultaneously, reducing boilerplate in CLI commands.
"""

from __future__ import annotations

import logging
from enum import Enum

from cli.console import (
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


def log_and_console(
    message: str,
    level: LogLevel = LogLevel.INFO,
    logger_name: str | None = None,
    stacklevel: int = 2,
) -> None:
    """Log message to file and display on console simultaneously.

    This helper reduces boilerplate by combining logger.{level}() and
    console_{level}() calls into a single function.

    Args:
        message: Message to log and display
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL, SUCCESS)
        logger_name: Logger name to use (None for root logger)
        stacklevel: How many frames up the stack to look for the caller (default 2)

    Example:
        log_and_console("Import completed", LogLevel.SUCCESS, "importer")
        log_and_console("Processing file...", LogLevel.INFO)
    """
    logger = logging.getLogger(logger_name) if logger_name else logging.getLogger()

    log_method = _LOGGER_METHODS.get(level, "info")
    getattr(logger, log_method)(message, stacklevel=stacklevel)

    console_func = _CONSOLE_FUNCTIONS.get(level, console_print)
    console_func(message)


def debug_both(message: str, logger_name: str | None = None) -> None:
    """Log debug message to both file and console.

    Args:
        message: Debug message
        logger_name: Logger name (None for root logger)
    """
    log_and_console(message, LogLevel.DEBUG, logger_name, stacklevel=3)


def info_both(message: str, logger_name: str | None = None) -> None:
    """Log info message to both file and console.

    Args:
        message: Info message
        logger_name: Logger name (None for root logger)
    """
    log_and_console(message, LogLevel.INFO, logger_name, stacklevel=3)


def warning_both(message: str, logger_name: str | None = None) -> None:
    """Log warning message to both file and console.

    Args:
        message: Warning message
        logger_name: Logger name (None for root logger)
    """
    log_and_console(message, LogLevel.WARNING, logger_name, stacklevel=3)


def error_both(message: str, logger_name: str | None = None) -> None:
    """Log error message to both file and console.

    Args:
        message: Error message
        logger_name: Logger name (None for root logger)
    """
    log_and_console(message, LogLevel.ERROR, logger_name, stacklevel=3)


def critical_both(message: str, logger_name: str | None = None) -> None:
    """Log critical message to both file and console.

    Args:
        message: Critical message
        logger_name: Logger name (None for root logger)
    """
    log_and_console(message, LogLevel.CRITICAL, logger_name, stacklevel=3)


def success_both(message: str, logger_name: str | None = None) -> None:
    """Log success message to both file and console.

    Args:
        message: Success message
        logger_name: Logger name (None for root logger)
    """
    log_and_console(message, LogLevel.SUCCESS, logger_name, stacklevel=3)
