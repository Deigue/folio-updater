"""Talking to the terminal: printing, prompting, progress, size.

Infrastructure, not presentation. Any layer may print, so this sits near the
bottom and imports nothing above it. Report rendering lives in `ui`, which is
at the top and may not be imported by anything below it.
"""


from term.console import (
    console_error,
    console_info,
    console_panel,
    console_print,
    console_rule,
    console_success,
    console_warning,
    get_symbol,
    supports_unicode,
)

__all__ = [
    "console_error",
    "console_info",
    "console_panel",
    "console_print",
    "console_rule",
    "console_success",
    "console_warning",
    "get_symbol",
    "supports_unicode",
]
