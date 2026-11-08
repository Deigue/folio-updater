"""The stored sheets: transactions, FX rates and tickers, straight from the database."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import INTERNAL_COLUMNS, Column
from exporters.excel_style import STORED_TAB
from exporters.sheet_spec import Col, Fmt, Row, Sheet, SheetTable, row_of
from models import txns_frame

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from models import FxRate, Quote, Txn

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
_VALUATION = "Valuation"
_DIVIDENDS = "Dividends"
_TRADING = "Trading"
_FUND = "Fund"
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
    Col("Market cap", Fmt.LARGE, group=_VALUATION),
    Col("P/E", Fmt.RATIO, group=_VALUATION),
    Col("Fwd P/E", Fmt.RATIO, group=_VALUATION),
    Col("EPS", Fmt.PRICE, group=_VALUATION),
    Col("Beta", Fmt.RATIO, group=_VALUATION),
    Col("Earnings", Fmt.DATE, group=_VALUATION),
    Col("Yield", Fmt.PERCENT, group=_DIVIDENDS),
    Col("Dividend", Fmt.PRICE, group=_DIVIDENDS),
    Col("Last paid", Fmt.PRICE, group=_DIVIDENDS),
    Col("Ex-dividend", Fmt.DATE, group=_DIVIDENDS),
    Col("52w low", Fmt.PRICE, group=_TRADING),
    Col("52w high", Fmt.PRICE, group=_TRADING),
    Col("50d avg", Fmt.PRICE, group=_TRADING),
    Col("200d avg", Fmt.PRICE, group=_TRADING),
    Col("Volume", Fmt.COUNT, group=_TRADING),
    Col("Avg volume", Fmt.COUNT, group=_TRADING),
    Col("Expense", Fmt.PERCENT, group=_FUND),
    Col("Assets", Fmt.LARGE, group=_FUND),
    Col("Category", width=18, group=_FUND),
    Col("Family", width=22, group=_FUND),
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
            symbol it resolves to today. The two differ across a rename.
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
    facts = quote.fundamentals
    cells.update(
        {
            "Name": quote.name,
            "$": str(quote.currency) if quote.currency else None,
            "Last": quote.price,
            "Day%": quote.day_change_pct,
            "vs 52w high": quote.from_high_52,
            "Market cap": quote.market_cap,
            "P/E": facts.trailing_pe,
            "Fwd P/E": facts.forward_pe,
            "EPS": facts.eps,
            "Beta": facts.beta,
            "Earnings": facts.earnings_date,
            "Yield": facts.dividend_yield,
            "Dividend": facts.dividend_rate,
            "Last paid": facts.last_dividend,
            "Ex-dividend": facts.ex_dividend_date,
            "52w low": facts.low_52,
            "52w high": facts.high_52,
            "50d avg": facts.avg_50,
            "200d avg": facts.avg_200,
            "Volume": facts.volume,
            "Avg volume": facts.avg_volume,
            "Expense": facts.expense_ratio,
            "Assets": facts.total_assets,
            "Category": facts.category,
            "Family": facts.fund_family,
            "Type": facts.quote_type,
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
