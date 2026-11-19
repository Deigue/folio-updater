"""The stored sheets: transactions, FX rates and tickers, straight from the database."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import INTERNAL_COLUMNS, Column
from exporters.catalog import FUNDAMENTALS
from exporters.excel_style import STORED_TAB
from exporters.sheet_spec import (
    TXN_FORMATS,
    Col,
    Fmt,
    Row,
    Sheet,
    SheetTable,
    read_cells,
    row_of,
)
from models import txns_frame

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from models import FxRate, Quote, Txn

# Columns whose zero says nothing at all.
TXN_QUIET = frozenset(
    {str(Column.Txn.PRICE), str(Column.Txn.UNITS), str(Column.Txn.FEE)},
)

FX_FORMATS: dict[str, Fmt] = {
    str(Column.FX.DATE): Fmt.DATE,
    str(Column.FX.FXUSDCAD): Fmt.RATE,
    str(Column.FX.FXCADUSD): Fmt.RATE,
}


def transactions_sheet(
    txns: Sequence[Txn],
    name: str,
    *,
    hidden: bool = False,
) -> Sheet:
    """Print the stored transactions as a sheet, ready to be imported again.

    Args:
        txns: Every stored transaction, in the order the sheet lists them.
        name: What the sheet is called. `folio import` looks for it by name.
        hidden: Keep it out of the tab strip. A workbook's Ledger already
            shows every one of these columns to a reader; this sheet is for
            the importer, which finds it by name either way.

    Returns:
        The sheet, one row per transaction, its heading on the first row, and
        nothing written below it that an importer could mistake for a row.
    """
    frame = txns_frame(txns)
    keys = [column for column in frame.columns if column not in INTERNAL_COLUMNS]
    return Sheet(
        name=name,
        tables=(
            SheetTable(
                tuple(
                    Col(key, TXN_FORMATS.get(key, Fmt.TEXT), quiet=key in TXN_QUIET)
                    for key in keys
                ),
                tuple(
                    Row(values)
                    for values in frame[keys].itertuples(index=False, name=None)
                ),
            ),
        ),
        tab_color=STORED_TAB,
        hidden=hidden,
    )


def fx_sheet(rates: Sequence[FxRate], name: str) -> Sheet:
    """Print the FX rates as a sheet, one row per date.

    Args:
        rates: Every rate held, oldest first.
        name: What the sheet is called.

    Returns:
        The sheet, oldest date first.
    """
    return Sheet(
        name=name,
        tables=(
            SheetTable(
                tuple(Col(key, fmt) for key, fmt in FX_FORMATS.items()),
                tuple(
                    Row((rate.date, float(rate.usd_cad), float(rate.cad_usd)))
                    for rate in rates
                ),
            ),
        ),
        tab_color=STORED_TAB,
    )


_QUOTE = "Quote"
_REFERENCE = "Reference"

# Arranged for a screen's width: what compares across tickers comes first, and
# what only identifies one (its type, exchange, fetch dates, and the ticker as
# the transactions spell it) waits at the right.
TICKER_COLUMNS: tuple[Col, ...] = (
    Col("Symbol"),
    Col("Name", width=22),
    Col("$", width=6, group=_QUOTE),
    Col("Last", Fmt.PRICE, group=_QUOTE),
    Col("Day%", Fmt.PERCENT_SIGNED, group=_QUOTE),
    Col("vs 52w high", Fmt.PERCENT_SIGNED, group=_QUOTE),
    *FUNDAMENTALS,
    Col("Type", group=_REFERENCE),
    Col("Sector", width=16, group=_REFERENCE),
    Col("Exchange", group=_REFERENCE),
    Col("Price as of", Fmt.DATE, group=_REFERENCE),
    Col("Fundamentals as of", Fmt.DATE, group=_REFERENCE),
    Col(str(Column.Ticker.TICKER), group=_REFERENCE),
)


def traded_tickers_sheet(
    tickers: Sequence[tuple[str, str]],
    quotes: Mapping[str, Quote],
    name: str,
) -> Sheet:
    """List every ticker the folio has traded, with its latest quote and fundamentals.

    Args:
        tickers: Each ticker as the transactions spell it, paired with the
            symbol it resolved to when traded. The two differ across a rename.
        quotes: The quote cache, keyed by resolved symbol. A ticker with no
            cached quote still gets its row.
        name: What the sheet is called.

    Returns:
        The sheet, one row per ticker, by symbol. A renamed security keeps a row
        per ticker it traded under, told apart by the `Ticker` at the far right.
    """
    rows = [
        row_of(TICKER_COLUMNS, _ticker_cells(ticker, symbol, quotes.get(symbol)))
        for ticker, symbol in sorted(tickers, key=lambda pair: (pair[1], pair[0]))
    ]
    return Sheet(
        name=name,
        tables=(SheetTable(TICKER_COLUMNS, tuple(rows)),),
        tab_color=STORED_TAB,
        freeze=2,  # `Symbol` and `Name` say which row is which
    )


def _ticker_cells(
    ticker: str,
    symbol: str,
    quote: Quote | None,
) -> dict[str, object]:
    """Read one traded ticker's quote and fundamentals, blank when uncached."""
    cells: dict[str, object] = {"Symbol": symbol, str(Column.Ticker.TICKER): ticker}
    if quote is None:
        return cells
    cells.update(read_cells(FUNDAMENTALS, quote))
    cells.update(
        {
            "Name": quote.name,
            "$": str(quote.currency) if quote.currency else None,
            "Last": quote.price,
            "Day%": quote.day_change_pct,
            "vs 52w high": quote.from_high_52,
            "Type": quote.fundamentals.quote_type,
            "Sector": quote.sector,
            "Exchange": quote.exchange,
            "Price as of": _date_of(quote.fetched_at),
            "Fundamentals as of": _date_of(quote.meta_fetched_at),
        },
    )
    return cells


def _date_of(moment: datetime | None) -> str | None:
    """Date a fetch by the day it happened, which is what ages it."""
    return None if moment is None else moment.date().isoformat()
