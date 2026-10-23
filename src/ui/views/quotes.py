"""Rendering the quote cache: what is cached, how old it is, and whether it is held."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.table import Table as RichTable

from ui.cells import EM_DASH, price, signed
from ui.format import short_age
from ui.layout.fit import fit_table
from ui.vocabulary import TABLE_BORDER, TABLE_HEADER

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from services.quotes_service import Quote


def quotes_table(
    quotes: Sequence[Quote],
    held: Collection[str] | None = None,
) -> RichTable:
    """Render the quote cache with each row's age.

    Args:
        quotes: The cached quotes to show.
        held: The symbols the folio holds, so a quote kept for a sold holding
            or a symbol only looked up stands out. None leaves the column out.

    Returns:
        The fitted table.
    """
    table = RichTable(
        title="Cached Quotes",
        show_header=True,
        header_style=TABLE_HEADER,
        border_style=TABLE_BORDER,
    )
    table.add_column("Symbol", no_wrap=True)
    if held is not None:
        table.add_column("Held", no_wrap=True)
    table.add_column("Yahoo", no_wrap=True)
    table.add_column("Name")
    table.add_column("Price", justify="right")
    table.add_column("Prev", justify="right")
    table.add_column("$", no_wrap=True)
    table.add_column("Age", justify="right", no_wrap=True)
    table.add_column("Status", no_wrap=True)

    for quote in sorted(quotes, key=lambda q: q.symbol):
        owned = [] if held is None else [_held(quote.symbol in held)]
        table.add_row(
            quote.symbol,
            *owned,
            quote.ysymbol,
            quote.name or "",
            signed(price(quote.price), quote.day_change),
            price(quote.prev_close),
            str(quote.currency) if quote.currency else "",
            _age(quote),
            _status(quote),
        )
    return fit_table(table, ("Name", "Prev", "Yahoo"))


def _held(owned: bool) -> str:  # noqa: FBT001 - one flag, read as a cell
    """Say whether the folio holds a cached symbol, dimming the ones it does not."""
    return "yes" if owned else "[dim]no[/dim]"


def _age(quote: Quote) -> str:
    """Render how long ago a quote was fetched, highlighting a stale one."""
    age = quote.age
    if age is None:
        return EM_DASH
    text = short_age(age.total_seconds())
    return f"[yellow]{text}[/yellow]" if quote.is_stale else text


def _status(quote: Quote) -> str:
    """Colour a quote's status by whether it is usable."""
    text = str(quote.status)
    if quote.priced:
        return f"[green]{text}[/green]"
    return f"[red]{text}[/red]"
