"""Turning a value into the text a cell shows.

Nothing here prints. These are the small conversions every table needs: a
pandas NA into a blank, a NUMERIC(20,10) into a column-stable figure, and a
cache timestamp into a phrase a reader can judge.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, NamedTuple

import pandas as pd

from domain import TORONTO_TZ
from ui.cells import WARN_GLYPH, display_round, short_date
from ui.vocabulary import (
    AGE_DAYS_PER_MONTH,
    AGE_DAYS_PER_YEAR,
    AGE_MONTHS_PER_YEAR,
    AGE_WEEKS_FROM_DAYS,
    CACHED_ICON,
    FRESH_ICON,
    FRESHNESS_SEPARATOR,
)

if TYPE_CHECKING:
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


def stamped(moment: datetime, verb: str) -> str:
    """Stamp a moment with its age: `2026-05-12 09:30:00 (modified 6mo ago)`."""
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
    if days < AGE_WEEKS_FROM_DAYS:
        return f"{days}d"
    if days < AGE_DAYS_PER_MONTH:
        return f"{days // 7}w"
    months = days // AGE_DAYS_PER_MONTH
    if months < AGE_MONTHS_PER_YEAR:
        return f"{months}mo"
    return f"{max(days // AGE_DAYS_PER_YEAR, 1)}y"


class _Part(NamedTuple):
    """One thing a freshness line or note ages.

    Attributes:
        label: What it is: `acb`, `quotes`, `fx rate` or `updated`.
        phrase: How current it is, such as `cached 2h ago` or `Nov 4 (12d ago)`.
        warn: Whether it is stale, or a cache holding nothing.
        cached: For a cache, whether it was read rather than just computed;
            None for what is not a cache (the FX rate, the last update).
    """

    label: str
    phrase: str
    warn: bool
    cached: bool | None


def _freshness_parts(
    freshness: Freshness,
    *,
    with_acb: bool,
    with_quotes: bool,
    with_update: bool,
) -> list[_Part]:
    """List what a report ages, in order. A current FX rate is left out."""
    parts = []
    if with_acb:
        replayed = freshness.replayed_at
        parts.append(
            _Part("acb", format_freshness(replayed), False, replayed is not None),
        )
    if with_quotes:
        quotes_at = freshness.quotes_at
        if quotes_at is None:
            parts.append(_Part("quotes", "nothing cached", True, False))
        else:
            phrase = format_freshness(quotes_at)
            parts.append(_Part("quotes", phrase, freshness.quotes_stale, True))
    if freshness.fx_date is not None and freshness.fx_stale:
        fx_day = short_date(date.fromisoformat(freshness.fx_date))
        parts.append(_Part("fx rate", f"from {fx_day}", True, None))
    if with_update and freshness.updated_at is not None:
        updated_at = freshness.updated_at
        day = short_date(updated_at.astimezone(TORONTO_TZ).date())
        phrase = f"{day} ({ago(updated_at)})"
        parts.append(_Part("updated", phrase, freshness.update_stale, None))
    return parts


def cache_badge(
    freshness: Freshness,
    *,
    offline: bool = False,
    with_acb: bool = True,
    with_quotes: bool = True,
    with_update: bool = True,
) -> str:
    """Compose the freshness line printed above a report's tables.

    One icon leads: a warning when any part is stale, a dot when every cache
    shown was just computed, else a clock. Stale parts are yellow and the rest
    dim. A current FX rate is left out, since the report's conversion note
    dates it.

    Args:
        freshness: How current the report's update, replay, quotes and FX rate are.
        offline: Whether the network was kept out of it, which is said.
        with_acb: Age the cost base. A report that read no folio leaves it out
            rather than claim a cost base it never used.
        with_quotes: Age the quotes, for a report that priced anything.
        with_update: Date the last update, for a report showing the folio's
            holdings or checks.

    Returns:
        A Rich-markup line such as `⏱ acb cached 2h ago · updated Nov 4 (12d ago)`,
        or empty when there is nothing to age.
    """
    parts = _freshness_parts(
        freshness,
        with_acb=with_acb,
        with_quotes=with_quotes,
        with_update=with_update,
    )
    if not parts:
        return ""
    caches = [part.cached for part in parts if part.cached is not None]
    if any(part.warn for part in parts):
        icon = f"[red]{WARN_GLYPH}[/red]"
    elif caches and not any(caches):
        icon = f"[green]{FRESH_ICON}[/green]"
    else:
        icon = f"[yellow]{CACHED_ICON}[/yellow]"
    separator = f" [dim]{FRESHNESS_SEPARATOR}[/dim] "
    line = separator.join(
        f"[{'yellow' if part.warn else 'dim'}]{part.label} {part.phrase}[/]"
        for part in parts
    )
    return f"{icon} {line}{' [dim](offline)[/dim]' if offline else ''}"


# How a sheet's note names what the line abbreviates.
_NOTE_LABELS = {"acb": "Cost base", "fx rate": "FX rate"}


def freshness_note(
    freshness: Freshness,
    *,
    with_quotes: bool = True,
    with_update: bool = True,
) -> str:
    """Say how current the replay, the quotes, a stale FX rate and the last update are.

    Args:
        freshness: How current the sheet's update, replay, quotes and FX rate are.
        with_quotes: Age the quotes, for a sheet that priced anything.
        with_update: Date the last update. A sheet the update itself writes
            leaves it out, since it would always say just now.

    Returns:
        One sentence, such as `Cost base computed just now; quotes cached 15m
        ago; updated Nov 4 (12d ago).`
    """
    parts = _freshness_parts(
        freshness,
        with_acb=True,
        with_quotes=with_quotes,
        with_update=with_update,
    )
    said = []
    for part in parts:
        # An empty cache says so already; only something with an age is stale.
        stale = " (stale)" if part.warn and part.cached is not False else ""
        label = _NOTE_LABELS.get(part.label, part.label)
        said.append(f"{label} {part.phrase}{stale}")
    return f"{'; '.join(said)}."
