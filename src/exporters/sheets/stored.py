"""The stored sheets: transactions, FX rates and tickers, straight from the database."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import Column
from exporters.excel_style import STORED_TAB
from exporters.table import Col, Fmt, Row, Table

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import pandas as pd

    from services.quotes_service import Quote

# How a stored transaction column reads. Anything unlisted, including a user's
# optional columns, is text.
TXN_FORMATS: dict[str, Fmt] = {
    str(Column.Txn.TXN_DATE): Fmt.DATE,
    str(Column.Txn.SETTLE_DATE): Fmt.DATE,
    str(Column.Txn.AMOUNT): Fmt.MONEY_SIGNED,
    str(Column.Txn.PRICE): Fmt.PRICE,
    str(Column.Txn.UNITS): Fmt.UNITS,
    str(Column.Txn.FEE): Fmt.MONEY,
    str(Column.Txn.SETTLE_CALCULATED): Fmt.ID,
}

_QUIET = frozenset(
    {str(Column.Txn.PRICE), str(Column.Txn.UNITS), str(Column.Txn.FEE)},
)

_INTERNAL = frozenset({str(Column.Txn.TXN_ID)})

FX_FORMATS: dict[str, Fmt] = {
    str(Column.FX.DATE): Fmt.DATE,
    str(Column.FX.FXUSDCAD): Fmt.RATE,
    str(Column.FX.FXCADUSD): Fmt.RATE,
}


def transactions_table(
    txns: pd.DataFrame,
    name: str,
    *,
    hidden: bool = False,
) -> Table:
    """Produce the stored transactions out, ready to be imported again.

    Args:
        txns: The `Txns` table, as read from the database.
        name: What the sheet is called. `folio import` looks for it by name.
        hidden: Keep it out of the tab strip. A workbook's Ledger already
            shows every one of these columns to a reader; this sheet is for
            the importer, which finds it by name either way.

    Returns:
        The table, one row per transaction, its heading on the first row, and
        nothing written below it that an importer could mistake for a row.
    """
    keys = [column for column in txns.columns if column not in _INTERNAL]
    return Table(
        name=name,
        columns=tuple(
            Col(key, TXN_FORMATS.get(key, Fmt.TEXT), quiet=key in _QUIET)
            for key in keys
        ),
        rows=tuple(
            Row(values) for values in txns[keys].itertuples(index=False, name=None)
        ),
        tab_color=STORED_TAB,
        hidden=hidden,
    )


def fx_table(rates: pd.DataFrame, name: str) -> Table:
    """Table for FX rates out, one row per date.

    Args:
        rates: The `FX` table, as read from the database.
        name: What the sheet is called.

    Returns:
        The table, oldest date first.
    """
    keys = list(rates.columns)
    ordered = rates.sort_values(str(Column.FX.DATE)) if keys else rates
    return Table(
        name=name,
        columns=tuple(Col(key, FX_FORMATS.get(key, Fmt.TEXT)) for key in keys),
        rows=tuple(
            Row(values) for values in ordered.itertuples(index=False, name=None)
        ),
        tab_color=STORED_TAB,
    )


TICKER_COLUMNS: tuple[Col, ...] = (
    Col(str(Column.Ticker.TICKER)),
    Col("Symbol"),
    Col("Name", width=24),
    Col("Sector", width=18),
    Col("Exchange"),
    Col("$"),
    Col("Last", Fmt.PRICE),
    Col("As of", Fmt.DATE),
)


def tickers_table(
    tickers: Sequence[tuple[str, str]],
    quotes: Mapping[str, Quote],
    name: str,
) -> Table:
    """List every ticker the folio has traded, with what is known about it.

    Args:
        tickers: Each ticker as the transactions spell it, paired with the
            symbol it resolves to today. The two differ across a rename.
        quotes: The quote cache, keyed by resolved symbol. A ticker with no
            cached quote still gets its row.
        name: What the sheet is called.

    Returns:
        The table, one row per ticker, `Ticker` first as it always was.
    """
    rows: list[Row] = []
    for ticker, symbol in tickers:
        quote = quotes.get(symbol)
        rows.append(
            Row(
                (
                    ticker,
                    symbol,
                    quote.name if quote else None,
                    quote.sector if quote else None,
                    quote.exchange if quote else None,
                    str(quote.currency) if quote and quote.currency else None,
                    quote.price if quote else None,
                    _as_of(quote),
                ),
            ),
        )
    return Table(
        name=name,
        columns=TICKER_COLUMNS,
        rows=tuple(rows),
        tab_color=STORED_TAB,
    )


def _as_of(quote: Quote | None) -> str | None:
    """Date a quote by when it was last fetched, which is what ages it."""
    if quote is None or quote.fetched_at is None:
        return None
    return quote.fetched_at.date().isoformat()
