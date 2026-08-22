"""Render logic for the dashboard related views."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from rich.console import Group
from rich.panel import Panel
from rich.table import Table as RichTable

from domain import Currency, WarningCode
from domain.numeric import ZERO, q2
from engine.positions import summarize_closed
from term import console_print, supports_unicode
from ui.layout.fit import fit, fit_table
from ui.layout.tiles import Block
from ui.vocabulary import MONEY_PRECISION, PRICE_PRECISION, UNIT_PRECISION

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Sequence

    from rich.console import RenderableType
    from rich.table import JustifyMethod

    from engine.flows import Flows
    from engine.positions import CurrencyTotals, Holding, HoldingSet
    from services.quotes_service import Quote
    from ui.layout.fit import FitResult

_WARN_GLYPH = "⚠" if supports_unicode() else "!"
_EM_DASH = "—" if supports_unicode() else "-"

_PERCENT_PRECISION = 2

# Diagnostics that make a pool's units untrustworthy.
_UNITS_SUSPECT = frozenset(
    {
        WarningCode.OVERSELL,
        WarningCode.NEGATIVE_FINAL_POSITION,
    },
)

def _price(value: Decimal | None) -> str:
    """Render a per-unit price at its own, finer precision."""
    if value is None:
        return _EM_DASH
    return f"{float(value):,.{PRICE_PRECISION}f}".rstrip("0").rstrip(".")


def quotes_table(quotes: Sequence[Quote]) -> RichTable:
    """Render the quote cache with each row's age.

    Args:
        quotes: The cached quotes to show.

    Returns:
        The fitted table.
    """
    table = RichTable(
        title="Cached Quotes",
        show_header=True,
        header_style="bold bright_white",
        border_style="bright_blue",
    )
    table.add_column("Symbol", no_wrap=True)
    table.add_column("Yahoo", no_wrap=True)
    table.add_column("Name")
    table.add_column("Price", justify="right")
    table.add_column("Prev", justify="right")
    table.add_column("$", no_wrap=True)
    table.add_column("Age", justify="right", no_wrap=True)
    table.add_column("Status", no_wrap=True)

    for quote in sorted(quotes, key=lambda q: q.symbol):
        table.add_row(
            quote.symbol,
            quote.ysymbol,
            quote.name or "",
            _signed(_price(quote.price), quote.day_change),
            _price(quote.prev_close),
            str(quote.currency) if quote.currency else "",
            _age(quote),
            _status(quote),
        )
    return fit_table(table, ("Name", "Prev", "Yahoo"))


def _age(quote: Quote) -> str:
    """Render how long ago a quote was fetched, highlighting a stale one."""
    age = quote.age
    if age is None:
        return _EM_DASH
    seconds = int(age.total_seconds())
    if seconds < 60:  # noqa: PLR2004 - a minute, in seconds
        text = "just now"
    elif seconds < 3600:  # noqa: PLR2004 - an hour, in seconds
        text = f"{seconds // 60}m"
    elif seconds < 86400:  # noqa: PLR2004 - a day, in seconds
        text = f"{seconds // 3600}h"
    else:
        text = f"{seconds // 86400}d"
    return f"[yellow]{text}[/yellow]" if quote.is_stale else text


def _status(quote: Quote) -> str:
    """Colour a quote's status by whether it is usable."""
    text = str(quote.status)
    if quote.priced:
        return f"[green]{text}[/green]"
    return f"[red]{text}[/red]"
