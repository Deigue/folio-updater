"""Rendering the quote cache: what is cached, how old it is, and whether it is held."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.table import Table as RichTable

from exporters.sheet_spec import Fmt
from services.quote_service import QuoteService
from ui.cells import EM_DASH, price, signed
from ui.format import short_age
from ui.layout.columns import FormattedColumn, add_column
from ui.layout.fit import fit_table
from ui.vocabulary import TABLE_BORDER, TABLE_HEADER

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from models import Quote

_SYMBOL = FormattedColumn("Symbol", no_wrap=True)
_HELD = FormattedColumn("Held", no_wrap=True)
# Everything after the symbol and whether it is held, in display order.
_QUOTE_COLUMNS = (
    FormattedColumn("Provider", no_wrap=True),
    FormattedColumn("Name"),
    FormattedColumn("Price", fmt=Fmt.PRICE, justify="right"),
    FormattedColumn("Prev", fmt=Fmt.PRICE, justify="right"),
    FormattedColumn("$", fmt=Fmt.CURRENCY, no_wrap=True),
    FormattedColumn("Age", justify="right", no_wrap=True),
    FormattedColumn("Status", no_wrap=True),
)


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
    columns = (
        _SYMBOL,
        *((_HELD,) if held is not None else ()),
        *_QUOTE_COLUMNS,
    )
    for column in columns:
        add_column(table, column)

    for quote in sorted(quotes, key=lambda q: q.symbol):
        owned = [] if held is None else [_held(quote.symbol in held)]
        table.add_row(
            quote.symbol,
            *owned,
            quote.provider_symbol,
            quote.name or "",
            signed(price(quote.price), quote.day_change),
            price(quote.prev_close),
            str(quote.currency) if quote.currency else "",
            _age(quote),
            _status(quote),
        )
    return fit_table(table, ("Name", "Prev", "Provider"))


def _held(owned: bool) -> str:  # noqa: FBT001 - one flag, read as a cell
    """Say whether the folio holds a cached symbol, dimming the ones it does not."""
    return "yes" if owned else "[dim]no[/dim]"


def _age(quote: Quote) -> str:
    """Render how long ago a quote was fetched, highlighting a stale one."""
    age = quote.age
    if age is None:
        return EM_DASH
    text = short_age(age.total_seconds())
    return f"[yellow]{text}[/yellow]" if QuoteService.is_stale(quote) else text


def _status(quote: Quote) -> str:
    """Colour a quote's status by whether it is usable."""
    text = str(quote.status)
    if quote.priced:
        return f"[green]{text}[/green]"
    return f"[red]{text}[/red]"
