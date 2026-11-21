"""The ticker sheets: one security in focus, and holdings compared over every range.

Every figure is read off the engine: the quote, its moves and the holdings the
`folio ticker` view prints. Nothing is computed here, only arranged.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from domain import CHARTED_RANGES, TORONTO_TZ, Currency, Fmt, PriceRange
from exporters.catalog import (
    FUNDAMENTAL_SECTIONS,
    FUNDAMENTALS,
    FUNDAMENTALS_BY_KEY,
    holding_cells,
    pool_columns,
)
from exporters.excel_style import OVERVIEW_TAB
from exporters.sheet_spec import (
    Col,
    Line,
    Role,
    Sheet,
    SheetList,
    SheetTable,
    read_cells,
    row_of,
)
from exporters.sheets.dashboard import POOL

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from engine.holdings import Holding
    from engine.performance import RangeMove
    from engine.ticker import PerformanceRow, PoolLine, SymbolHoldings
    from models import Quote

MOVE_COLUMNS: tuple[Col, ...] = (
    Col("Range", width=8),
    Col("From", Fmt.DATE),
    Col("Start", Fmt.PRICE),
    Col("Chg", Fmt.PRICE_SIGNED),
    Col("Chg%", Fmt.PERCENT_SIGNED),
)

_POOLED = "POOLED"
_MATRIX_NOTE = (
    "Market values in CAD. Moves are price only, each in the security's own "
    "currency, from an official close to the latest price."
)
_MOVES_NOTE = "Every move runs from an official close to the latest price, price only."
_CRA = "CRA"

PERFORMANCE_SHEET = "Performance"


def ticker_sheet(  # noqa: PLR0913
    quote: Quote,
    moves: Mapping[PriceRange, RangeMove | None],
    per_currency: Sequence[SymbolHoldings],
    *,
    aliases: Sequence[str] = (),
    by_account: bool = False,
    notes: Sequence[str] = (),
) -> Sheet:
    """Print one security on one sheet: quote, fundamentals, moves, holdings.

    Args:
        quote: Its quote, fundamentals included.
        moves: Its move over every range.
        per_currency: Its holdings across the folio, one per currency shown.
        aliases: Other symbols the folio has known it by.
        by_account: The holdings list broker accounts rather than types.
        notes: Lines to disclose, such as how old the prices are.

    Returns:
        The sheet: the quote and fundamentals as lists, the moves as its first
        table, and each holdings table below that.
    """
    rows = tuple(
        row_of(MOVE_COLUMNS, _move_cells(price_range, moves.get(price_range)))
        for price_range in CHARTED_RANGES
    )
    return Sheet(
        name=quote.symbol,
        tables=(
            SheetTable(
                MOVE_COLUMNS,
                rows,
                notes=(
                    *notes,
                    *_first_buy_note(per_currency),
                    _MOVES_NOTE,
                ),
            ),
            *(
                _holdings_table(holdings, quote.currency, by_account=by_account)
                for holdings in per_currency
            ),
        ),
        lists=(_quote_list(quote, aliases), _fundamentals_list(quote)),
        tab_color=OVERVIEW_TAB,
        # The quote and fundamentals lists are too tall to pin above it.
        freeze_rows=False,
    )


def _first_buy_note(per_currency: Sequence[SymbolHoldings]) -> list[str]:
    """Say when and at what the folio first bought the security."""
    bought = per_currency[0].first_buy if per_currency else None
    if bought is None:
        return []
    split = " (before a later split)" if bought.split_since else ""
    return [f"First bought {bought.date} at {bought.price} {bought.currency}{split}."]


def performance_sheet(
    rows: Sequence[PerformanceRow],
    *,
    name: str = PERFORMANCE_SHEET,
    pool_weight: bool = False,
    notes: Sequence[str] = (),
) -> Sheet:
    """Line securities up over every range, one row each, to sort and filter.

    Args:
        rows: The securities, in the order to list them.
        name: What the sheet is called.
        pool_weight: Carry each security's share of a narrowed pool as well as
            of the whole folio.
        notes: Lines to disclose, such as how old the prices are.

    Returns:
        The sheet: the move over each range as a column of its own.
    """
    columns: list[Col] = [
        Col("Symbol"),
        Col("$", width=6),
        Col("Last", Fmt.PRICE),
        Col("Market", Fmt.MONEY),
    ]
    if pool_weight:
        columns.append(Col("Wt%", Fmt.PERCENT))
    columns.append(Col("Folio%", Fmt.PERCENT, bar=True))
    columns.extend(
        Col(str(price_range), Fmt.PERCENT_SIGNED) for price_range in CHARTED_RANGES
    )
    built = tuple(columns)
    return Sheet(
        name=name,
        tables=(
            SheetTable(
                built,
                tuple(row_of(built, _performance_cells(row)) for row in rows),
                notes=(*notes, _MATRIX_NOTE),
            ),
        ),
        tab_color=OVERVIEW_TAB,
    )


def _performance_cells(row: PerformanceRow) -> dict[str, object]:
    """Read one security's line of the comparison."""
    quote = row.quote
    cells: dict[str, object] = {
        "Symbol": row.symbol,
        "$": str(quote.currency) if quote.currency else None,
        "Last": quote.price,
        "Market": row.market,
        "Wt%": row.weight_in_pool,
        "Folio%": row.weight_in_folio,
    }
    for price_range in CHARTED_RANGES:
        move = row.moves.get(price_range)
        cells[str(price_range)] = None if move is None else move.change_pct
    return cells


def _quote_list(quote: Quote, aliases: Sequence[str]) -> SheetList:
    """Name the security and give its price and the day's move."""
    lines = [
        Line("Name", quote.name),
        Line("Last", quote.price, Fmt.PRICE, str(quote.currency or "")),
        Line("Change", quote.day_change, Fmt.PRICE_SIGNED),
        Line("Change%", quote.day_change_pct, Fmt.PERCENT_SIGNED),
        Line("Prev close", quote.prev_close, Fmt.PRICE),
        Line("Sector", quote.sector),
        Line("Exchange", quote.exchange),
        Line("Type", quote.fundamentals.quote_type),
    ]
    if aliases:
        lines.append(Line("Also traded as", ", ".join(aliases)))
    return SheetList(
        title=f"{quote.symbol}",
        lines=tuple(line for line in lines if line.value is not None),
    )


def _fundamentals_list(quote: Quote) -> SheetList:
    """List every fundamental the provider reported, by section, blanks left out."""
    values = read_cells(FUNDAMENTALS, quote)
    lines: list[Line] = []
    for heading, keys in FUNDAMENTAL_SECTIONS:
        columns = [FUNDAMENTALS_BY_KEY[key] for key in keys]
        present = [
            Line(column.full_label, values[column.header], column.fmt)
            for column in columns
            if values[column.header] is not None
        ]
        if present:
            lines.extend((Line(heading), *present))
    return SheetList(title="Fundamentals", lines=tuple(lines))


def _move_cells(
    price_range: PriceRange,
    move: RangeMove | None,
) -> dict[str, object]:
    """Read one range's move: where it began, and how far it has come."""
    if move is None:
        return {"Range": str(price_range)}
    return {
        "Range": str(price_range),
        "From": _start(move.start),
        "Start": move.anchor,
        "Chg": move.change,
        "Chg%": move.change_pct,
    }


def _start(start: str) -> str | None:
    """Give where a range starts: a date, or for `2h` a time on Toronto's clock."""
    if not start:
        return None
    if len(start) == len("YYYY-MM-DD"):
        return start
    return (
        datetime.fromisoformat(start).astimezone(TORONTO_TZ).strftime("%Y-%m-%d %H:%M")
    )


def _holdings_table(
    holdings: SymbolHoldings,
    quoted_in: Currency | None,
    *,
    by_account: bool,
) -> SheetTable:
    """Print every pool's holding, with the folio-wide pooled figures.

    Args:
        holdings: The security across the folio, in one currency.
        quoted_in: The currency the security trades in, so a table converted
            out of it can say at what rate.
        by_account: The lines are broker accounts rather than types.

    Returns:
        The table, the pooled figures last.
    """
    share = "Acct%" if by_account else "Type%"
    columns: tuple[Col, ...] = (Col(POOL, width=20), *pool_columns(share))
    rows = [
        row_of(
            columns,
            _holding_cells(_pool_name(line), line.holding, columns),
            Role.CLOSED if line.holding.closed else Role.DATA,
        )
        for line in holdings.lines
    ]
    if holdings.pooled_differs:
        pooled = _holding_cells(_POOLED, holdings.total, columns)
        pooled[share] = None
        rows.append(row_of(columns, pooled, Role.TOTAL))
    notes: list[str] = []
    if holdings.fx_rate is not None and holdings.currency is not quoted_in:
        notes.append(f"Market value at USDCAD {holdings.fx_rate} ({holdings.fx_date}).")
    return SheetTable(
        columns,
        tuple(rows),
        notes=tuple(notes),
        title=f"{holdings.symbol} held · {holdings.currency}",
    )


def _pool_name(line: PoolLine) -> str:
    """Name a pool, badging one whose sales the CRA taxes."""
    return f"{line.pool.label} {_CRA}" if line.taxed else line.pool.label


def _holding_cells(
    pool: str,
    holding: Holding,
    columns: Sequence[Col[Holding]],
) -> dict[str, object]:
    """Read one pool's holding. A closed one keeps only what it earned."""
    label = f"{pool} (closed)" if holding.closed else pool
    return {POOL: label, **holding_cells(columns, holding)}
