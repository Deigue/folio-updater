"""What an empty cell is, whatever shape it arrived in."""

from __future__ import annotations

import pandas as pd


def is_blank(value: object) -> bool:
    """Report whether a cell holds nothing, whatever the source called it.

    A frame cell arrives as `NaN` or `pd.NA`, an engine figure as `None`. All
    three mean the same thing to a reader, and all three must stay empty rather
    than print as a zero.

    Args:
        value: The stored value.

    Returns:
        True when there is nothing to show.
    """
    if value is None:
        return True
    try:
        # A cell is `object`, which the stubs' overloads do not cover.
        return bool(pd.isna(value))  # ty: ignore[no-matching-overload]
    except (TypeError, ValueError):  # only arrays reach here
        return False
