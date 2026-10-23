"""Turning a value into the text a cell shows.

Nothing here prints. These are the small conversions every table needs: a
pandas NA into a blank, a NUMERIC(20,10) into a column-stable figure, and a
cache timestamp into a phrase a reader can judge.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

import pandas as pd

from term.console import supports_unicode
from ui.cells import WARN_GLYPH

if TYPE_CHECKING:
    from collections.abc import Container, Mapping, Sequence

    from services.quotes_service import Quote


def safe_str(value: Any) -> str:
    """Convert value to string, treating pandas NA as empty string.

    Args:
        value: Value to convert.

    Returns:
        String representation, empty string for NA/None values.
    """
    if pd.isna(value):
        return ""
    return str(value)


def decimals(value: Any, precision: int, minimum: int = 0) -> str:
    """Render a number at a capped precision, dropping the zeros it does not need.

    Transaction figures come out of a NUMERIC(20,10) column, so an unformatted
    cell can be anything from `1` to `1.12423836`. Capping the decimals keeps a
    column's width predictable from one page to the next.

    Args:
        value: Raw cell value, which need not be numeric.
        precision: Most decimal places to show.
        minimum: Fewest decimal places to keep, so a money-role column never
            strips below its cents.

    Returns:
        The formatted cell, the original text if it is not a number, or an
        empty string for a blank.
    """
    text = safe_str(value)
    if not text:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return text
    rendered = f"{number:,.{precision}f}"
    if precision == minimum:
        return rendered
    whole, _, fraction = rendered.partition(".")
    fraction = fraction.rstrip("0").ljust(minimum, "0")
    return f"{whole}.{fraction}" if fraction else whole


# Cache freshness thresholds, in seconds, for `format_freshness`.
_JUST_NOW = 60
_ONE_HOUR = 3600
_ONE_DAY = 86400


def format_freshness(computed_at: datetime | None) -> str:
    """Describe how current a cached figure is, in human terms.

    Every surface that can serve cached data has to say so, in its header or
    panel subtitle.

    Args:
        computed_at: When the data was computed, or None if it was computed by
            this invocation.

    Returns:
        A short phrase such as `computed just now` or `cached 4d ago`.
    """
    if computed_at is None:
        return "computed just now"

    now = datetime.now(computed_at.tzinfo)
    elapsed = max((now - computed_at).total_seconds(), 0)
    if elapsed < _JUST_NOW:
        return "cached just now"
    return f"cached {short_age(elapsed)} ago"


def short_age(seconds: float) -> str:
    """Render an elapsed time at its largest unit: `just now`, `4m`, `3h`, `2d`."""
    if seconds < _JUST_NOW:
        return "just now"
    if seconds < _ONE_HOUR:
        return f"{int(seconds // 60)}m"
    if seconds < _ONE_DAY:
        return f"{int(seconds // _ONE_HOUR)}h"
    return f"{int(seconds // _ONE_DAY)}d"


# Freshness icons
_FRESH_ICON, _CACHED_ICON = ("●", "⏱") if supports_unicode() else ("*", "~")


def freshness_badge(computed_at: datetime | None) -> str:
    """Render the freshness indicator as a coloured, iconed one-liner.

    Meant to be printed above table, since footer is invisible while paging.
    (Pager redraws in alternate screen, footer only prints after exited)

    Args:
        computed_at: When the data was computed, or None if it was computed
            by this invocation.

    Returns:
        A Rich-markup string such as `[green]●[/green] computed just now`.
    """
    return freshness_line([("", computed_at)])


def freshness_line(
    parts: Sequence[tuple[str, datetime | None]],
    *,
    warn: Container[str] = (),
    missing: Container[str] = (),
) -> str:
    """Age several independent caches on one line.

    Args:
        parts: `(label, computed_at)` per source, in the order to show them. An
            empty label renders the phrase alone, which is the single-source
            badge. `computed_at` of None means this invocation produced it.
        warn: Labels whose age is past its useful life, highlighted rather than
            merely dimmed.
        missing: Labels that hold nothing at all. Distinct from `computed_at`
            being None, which means "produced just now": an empty cache must
            not read as a fresh one.

    Returns:
        A Rich-markup string such as
        `[green]●[/green] [dim]acb computed just now[/dim] [dim]·[/dim] ...`.
    """
    rendered = [
        _one_freshness(
            label,
            computed_at,
            warned=label in warn,
            empty=label in missing,
        )
        for label, computed_at in parts
    ]
    return " [dim]·[/dim] ".join(rendered)


def _one_freshness(
    label: str,
    computed_at: datetime | None,
    *,
    warned: bool,
    empty: bool = False,
) -> str:
    """Render one source's icon and phrase."""
    text = "nothing cached" if empty else format_freshness(computed_at)
    phrase = f"{label} {text}" if label else text
    if warned or empty:
        return f"[red]{WARN_GLYPH}[/red] [yellow]{phrase}[/yellow]"
    if computed_at is None:
        return f"[green]{_FRESH_ICON}[/green] [dim]{phrase}[/dim]"
    return f"[yellow]{_CACHED_ICON}[/yellow] [dim]{phrase}[/dim]"


def quote_age(quotes: Mapping[str, Quote]) -> tuple[datetime | None, bool]:
    """Age the quote cache as a whole: its oldest row wins.

    Args:
        quotes: The quotes a report was priced with.

    Returns:
        When the oldest quote was fetched (None if every one is from this
        invocation), and whether any of them is past its TTL.
    """
    fetched = [quote.fetched_at for quote in quotes.values() if quote.fetched_at]
    stale = any(quote.is_stale for quote in quotes.values())
    if not fetched:
        return None, stale
    return min(fetched), stale


# Labels for the two caches a priced report ages.
_ACB_LABEL = "acb"
_QUOTES_LABEL = "quotes"


def cache_badge(
    quotes: Mapping[str, Quote],
    *,
    computed_at: datetime | None = None,
    offline: bool = False,
    with_acb: bool = True,
) -> str:
    """Compose the freshness line for a priced report.

    Args:
        quotes: The quotes the report was priced with.
        computed_at: When the cost base was computed, or None if just now.
        offline: Whether the network was kept out of it, which is said.
        with_acb: Age the cost base too. A report that read no folio leaves it
            out rather than claim a cost base it never used.

    Returns:
        A Rich-markup line for cache-freshness status.
    """
    quote_at, stale = quote_age(quotes)
    empty = not any(quote.fetched_at for quote in quotes.values())
    parts = [(_QUOTES_LABEL, quote_at)]
    if with_acb:
        parts.insert(0, (_ACB_LABEL, computed_at))
    line = freshness_line(
        parts,
        warn={_QUOTES_LABEL} if stale and not empty else set(),
        missing={_QUOTES_LABEL} if empty else set(),
    )
    return f"{line} [dim](offline)[/dim]" if offline else line


def freshness_note(
    computed_at: datetime | None,
    quotes: Mapping[str, Quote],
) -> str:
    """Say how old both caches are (cost base cache + quotes cache date).

    Args:
        computed_at: When the cost base was computed, or None if just now.
        quotes: The quotes the sheet was priced with.

    Returns:
        One sentence, such as `Cost base computed just now; quotes cached 15m
        ago.`
    """
    quote_at, _ = quote_age(quotes)
    empty = not any(quote.fetched_at for quote in quotes.values())
    aged = "nothing cached" if empty else format_freshness(quote_at)
    return f"Cost base {format_freshness(computed_at)}; quotes {aged}."
