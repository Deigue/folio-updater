"""CLI module for folio-updater.

This module exports the public API for CLI utilities including console output
functions and display classes.
"""

from cli.console import (
    console_error,
    console_info,
    console_panel,
    console_print,
    console_rule,
    console_success,
    console_warning,
)
from cli.display import (
    ProgressDisplay,
    TransactionDisplay,
)

__all__ = [
    "ProgressDisplay",
    "TransactionDisplay",
    "console_error",
    "console_info",
    "console_panel",
    "console_print",
    "console_rule",
    "console_success",
    "console_warning",
]
