"""Say something to the user and write it to the log in one call.

Import the module, not its functions: `announce.success(...)` reads as what it
does, and stays distinct from `console_success(...)`, which only prints.
"""

from __future__ import annotations

import logging

from term.console import (
    console_error,
    console_info,
    console_print,
    console_success,
    console_warning,
)


def debug(message: str, logger_name: str | None = None) -> None:
    """Log debug message to both file and console.

    Args:
        message: Debug message
        logger_name: Logger name (None for root logger)
    """
    logging.getLogger(logger_name).debug(message, stacklevel=2)
    console_print(message, "dim")


def info(message: str, logger_name: str | None = None) -> None:
    """Log info message to both file and console.

    Args:
        message: Info message
        logger_name: Logger name (None for root logger)
    """
    logging.getLogger(logger_name).info(message, stacklevel=2)
    console_info(message)


def warning(message: str, logger_name: str | None = None) -> None:
    """Log warning message to both file and console.

    Args:
        message: Warning message
        logger_name: Logger name (None for root logger)
    """
    logging.getLogger(logger_name).warning(message, stacklevel=2)
    console_warning(message)


def error(message: str, logger_name: str | None = None) -> None:
    """Log error message to both file and console.

    Args:
        message: Error message
        logger_name: Logger name (None for root logger)
    """
    logging.getLogger(logger_name).error(message, stacklevel=2)
    console_error(message)


def critical(message: str, logger_name: str | None = None) -> None:
    """Log critical message to both file and console.

    Args:
        message: Critical message
        logger_name: Logger name (None for root logger)
    """
    logging.getLogger(logger_name).critical(message, stacklevel=2)
    console_error(message)


def success(message: str, logger_name: str | None = None) -> None:
    """Log success message to both file and console.

    Args:
        message: Success message
        logger_name: Logger name (None for root logger)
    """
    logging.getLogger(logger_name).info(message, stacklevel=2)
    console_success(message)
