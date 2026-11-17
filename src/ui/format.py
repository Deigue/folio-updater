"""Turning a value into the text a cell shows.

Nothing here prints. These are the small conversions every table needs: a
pandas NA into a blank, a NUMERIC(20,10) into a column-stable figure, and a
cache timestamp into a phrase a reader can judge.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

import pandas as pd

from term.console import supports_unicode
from ui.cells import WARN_GLYPH, display_round

if TYPE_CHECKING:
    from collections.abc import Container, Sequence

    from engine.freshness import Freshness


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


def is_number(text: str) -> bool:
    """Whether text reads as a finite number; NaN and infinity do not count."""
    try:
        return Decimal(text).is_finite()
    except InvalidOperation:
        return False


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
        number = display_round(float(value), precision)
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
# Ages in days: under two weeks counts days, under a month weeks, under a year months.
_DAYS_AS_DAYS = 14
_DAYS_IN_MONTH = 30
_DAYS_IN_YEAR = 365


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
    return f"cached {ago(computed_at)}"


def stamped(moment: datetime | None, verb: str = "modified") -> str:
    """Stamp a moment with its age: `2026-05-12 09:30:00 (modified 6mo ago)`.

    None stands for a file not written yet, which says so.
    """
    if moment is None:
        return "not created yet"
    return f"{moment:%Y-%m-%d %H:%M:%S} ({verb} {ago(moment)})"


def ago(moment: datetime) -> str:
    """Say how long ago a moment was: `just now`, `4m ago`, `6mo ago`."""
    now = datetime.now(moment.tzinfo)
    age = short_age(max((now - moment).total_seconds(), 0))
    return age if age == "just now" else f"{age} ago"


def short_age(seconds: float) -> str:
    """Render an elapsed time at its largest unit: `just now`, `4m`, `3h`, `6mo`."""
    if seconds < _JUST_NOW:
        return "just now"
    if seconds < _ONE_HOUR:
        return f"{int(seconds // 60)}m"
    if seconds < _ONE_DAY:
        return f"{int(seconds // _ONE_HOUR)}h"
    return _days_age(int(seconds // _ONE_DAY))


def _days_age(days: int) -> str:
    """Render whole days as days, weeks, months or years: `13d`, `3w`, `6mo`, `1y`."""
    if days < _DAYS_AS_DAYS:
        return f"{days}d"
    if days < _DAYS_IN_MONTH:
        return f"{days // 7}w"
    if days < _DAYS_IN_YEAR:
        return f"{days // _DAYS_IN_MONTH}mo"
    return f"{days // _DAYS_IN_YEAR}y"


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


# Labels for the two caches a priced report ages.
_ACB_LABEL = "acb"
_QUOTES_LABEL = "quotes"


def cache_badge(
    freshness: Freshness,
    *,
    offline: bool = False,
    with_acb: bool = True,
) -> str:
    """Compose the freshness line for a priced report.

    Args:
        freshness: How current the report's replay, quotes and FX rate are.
        offline: Whether the network was kept out of it, which is said.
        with_acb: Age the cost base too. A report that read no folio leaves it
            out rather than claim a cost base it never used.

    Returns:
        A Rich-markup line for cache-freshness status. A current FX rate is
        left out: the report's own conversion note already dates it.
    """
    empty = freshness.quotes_at is None
    parts = [(_QUOTES_LABEL, freshness.quotes_at)]
    if with_acb:
        parts.insert(0, (_ACB_LABEL, freshness.replayed_at))
    line = freshness_line(
        parts,
        warn={_QUOTES_LABEL} if freshness.quotes_stale else set(),
        missing={_QUOTES_LABEL} if empty else set(),
    )
    if freshness.fx_stale:
        stale = f"[yellow]fx rate from {freshness.fx_date}[/yellow]"
        line += f" [dim]·[/dim] [red]{WARN_GLYPH}[/red] {stale}"
    return f"{line} [dim](offline)[/dim]" if offline else line


def freshness_note(freshness: Freshness) -> str:
    """Say how current the replay, the quotes and a stale FX rate are.

    Args:
        freshness: How current the sheet's replay, quotes and FX rate are.

    Returns:
        One sentence, such as `Cost base computed just now; quotes cached 15m
        ago.`
    """
    quotes = (
        "nothing cached"
        if freshness.quotes_at is None
        else format_freshness(freshness.quotes_at)
    )
    note = f"Cost base {format_freshness(freshness.replayed_at)}; quotes {quotes}"
    if freshness.fx_stale:
        note += f"; FX rate from {freshness.fx_date} (stale)"
    return f"{note}."
