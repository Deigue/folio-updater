"""Render one security: its quote, fundamentals, price moves and every holding."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from rich.panel import Panel
from rich.table import Table as RichTable
from rich.text import Text

from domain import TORONTO_TZ, AccountType, PriceRange
from engine.panels import account_type_of
from term import console_print
from term.size import terminal_size
from ui.cells import (
    EM_DASH,
    UNICODE,
    WARN_GLYPH,
    clock_time,
    currency_tint,
    day_move,
    graded,
    income,
    lifetime_return,
    long_date,
    money,
    percent,
    price,
    signed,
    style,
    units,
    weight,
)
from ui.layout.bars import position_bar
from ui.layout.charts import ChartStyle, area_chart
from ui.layout.fit import fit
from ui.layout.tiles import COLUMN_GAP, Block, TilingLayout
from ui.vocabulary import (
    ACCOUNT_TYPE_COLORS,
    FLAT,
    GAIN,
    GRAND_TOTAL_ROW_STYLE,
    LOSS,
    REFERENCE_STYLE,
    RETURN_STRONG_AT,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from rich.console import RenderableType

    from engine.performance import RangeMove
    from engine.positions import Holding
    from engine.ticker import PerformanceRow, PoolLine, SymbolPosition
    from services.quotes_service import Quote

# A fund reports a fund's figures; anything else reports a company's.
_FUND_TYPES = frozenset({"ETF", "MUTUALFUND"})

_RANGE_BAR_WIDTH = 6

# The fundamentals stay narrow so the charts get the remaining width.
_FUNDAMENTALS_WIDTH = 40
_LABEL_WIDTH = 13
_VALUE_WIDTH = 20

# Each chart: its rows, the fewest points worth drawing, and the most kept.
_CHART_ROWS = 5
_CHART_MIN_POINTS = 24
_CHART_MAX_POINTS = 60
_CHARTS_PER_ROW = (5, 4, 3, 2, 1)
# A chart panel's border and padding, around its points.
_CHART_FRAME = 4
# A move with nothing between its anchor and the live price has no shape.
_ENDS_ONLY = 2
# A move this many times the anchor reads as a multiple, not a percentage.
_MULTIPLE_FROM = Decimal(10)
_CHART_STYLE = ChartStyle(up=GAIN, down=LOSS, level=FLAT, guide="grey35")
_MAGNITUDES = (
    (Decimal(10) ** 12, "T"),
    (Decimal(10) ** 9, "B"),
    (Decimal(10) ** 6, "M"),
    (Decimal(10) ** 3, "K"),
)

_POOL_DROP_ORDER = ("Weight", "Book", "Unreal%", "Total%", "Divs", "Realized")

# Matrix columns, conceded in this order on a narrow terminal.
_MATRIX_DROP_ORDER = ("Wt%", "2d", "2h", "5y", "Market")
# Bolding the best and worst of a column means nothing with only one row.
_COMPARABLE = 2


def show_ticker(
    quote: Quote,
    moves: Mapping[PriceRange, RangeMove | None],
    positions: Sequence[SymbolPosition],
    *,
    aliases: Sequence[str] = (),
    by_account: bool = False,
    badge: str | None = None,
) -> None:
    """Print everything known about one security.

    Args:
        quote: Its live quote, fundamentals included.
        moves: Its price move over every range.
        positions: Its holdings across the folio, one per currency shown.
            Empty for a security the folio never traded.
        aliases: Other symbols the folio has known it by.
        by_account: The positions list broker accounts rather than types.
        badge: Cache-freshness line, printed above everything.
    """
    if badge:
        console_print(badge)
    console_print(_header(quote, aliases))

    # The fundamentals anchor the left; the chart grid is sized for the room
    # beside them, or for the whole width when it would not fit there.
    width, _ = terminal_size()
    blocks: list[Block] = []
    room = width
    facts = _fundamentals(quote)
    if facts is not None:
        blocks.append(_block("Fundamentals", facts))
        beside = width - blocks[0].width - COLUMN_GAP
        if beside >= _grid_width(2, _CHART_MIN_POINTS):
            room = beside
    blocks.append(_block("Performance", _chart_grid(moves, room)))
    TilingLayout(blocks, ordered=True).render()

    for position in positions:
        fitted = fit(_pool_table(position, by_account=by_account), _POOL_DROP_ORDER)
        if fitted.note:
            console_print(fitted.note)
        console_print(fitted.table)
        for note in _conversion_notes(position, quote):
            console_print(note)
    if positions:
        for note in _footer(positions[0]):
            console_print(note)


def _header(quote: Quote, aliases: Sequence[str]) -> Panel:
    """Name the security and give its price and the day's move."""
    name = f" · {quote.name}" if quote.name else ""
    described = [
        part
        for part in (
            quote.sector,
            quote.exchange,
            str(quote.currency) if quote.currency else None,
            quote.fundamentals.quote_type,
        )
        if part
    ]
    if quote.priced:
        change = quote.day_change
        arrow = "" if change is None or change == 0 else ("▲ " if change > 0 else "▼ ")
        line = (
            f"[bold]{price(quote.price)}[/bold] {quote.currency or ''}   "
            f"{signed(arrow + price(change), change)} "
            f"{day_move(quote.day_change_pct)}   "
            f"[dim]prev close {price(quote.prev_close)}[/dim]"
        )
    else:
        line = "[yellow]No price available.[/yellow]"
    lines = [line]
    if aliases:
        lines.append(f"[dim]Also traded as {', '.join(aliases)}[/dim]")
    return Panel(
        "\n".join(lines),
        title=f"[bold]{quote.symbol}[/bold]{name}",
        title_align="left",
        subtitle=" · ".join(described) or None,
        subtitle_align="right",
        border_style="bright_blue",
        expand=False,
    )


def _fundamentals(quote: Quote) -> RichTable | None:
    """Lay out every fundamental the provider reported, grouped, blanks left out."""
    facts = quote.fundamentals
    fund = facts.quote_type in _FUND_TYPES
    sections: list[tuple[str, list[tuple[str, str | None]]]] = [
        (
            "Fund" if fund else "Valuation",
            [
                ("Market cap", _magnitude(quote.market_cap)),
                ("Assets", _magnitude(facts.total_assets)),
                ("Expense ratio", _percent_or_none(facts.expense_ratio)),
                ("Category", facts.category),
                ("Family", facts.fund_family),
                ("P/E", _ratio(facts.trailing_pe)),
                ("Forward P/E", _ratio(facts.forward_pe)),
                ("EPS", _price_or_none(facts.eps)),
                ("Beta", _ratio(facts.beta)),
                ("Earnings", long_date(facts.earnings_date)),
            ],
        ),
        (
            "Dividends",
            [
                ("Dividend", _price_or_none(facts.dividend_rate)),
                ("Yield", _percent_or_none(facts.dividend_yield)),
                ("Last dividend", _price_or_none(facts.last_dividend)),
                ("Ex-dividend", long_date(facts.ex_dividend_date)),
            ],
        ),
        (
            "Trading",
            [
                ("52-week", _range(quote)),
                ("50-day avg", _price_or_none(facts.avg_50)),
                ("200-day avg", _price_or_none(facts.avg_200)),
                ("Volume", _volume(facts.volume, facts.avg_volume)),
            ],
        ),
    ]

    table = RichTable(
        title="Fundamentals",
        show_header=False,
        border_style="bright_blue",
        title_justify="left",
        width=_FUNDAMENTALS_WIDTH,
    )
    table.add_column(style=REFERENCE_STYLE, no_wrap=True, min_width=_LABEL_WIDTH)
    table.add_column(
        justify="right",
        no_wrap=True,
        overflow="ellipsis",
        max_width=_VALUE_WIDTH,
    )
    shown = False
    for heading, rows in sections:
        present = [(label, value) for label, value in rows if value]
        if not present:
            continue
        if shown:
            table.add_section()
        table.add_row(f"[bold]{heading}[/bold]", "")
        for label, value in present:
            table.add_row(label, value)
        shown = True
    return table if shown else None


def _range(quote: Quote) -> str | None:
    """Draw where the price sits in its 52-week range."""
    facts = quote.fundamentals
    if facts.low_52 is None or facts.high_52 is None:
        return None
    bar = ""
    if quote.price is not None:
        bar = position_bar(
            quote.price,
            facts.low_52,
            facts.high_52,
            _RANGE_BAR_WIDTH,
            unicode=UNICODE,
        )
    return f"{price(facts.low_52)} {bar} {price(facts.high_52)}"


def _volume(volume: Decimal | None, average: Decimal | None) -> str | None:
    """Give the last session's volume beside the average."""
    if volume is None:
        return None
    if average is None:
        return _magnitude(volume)
    return f"{_magnitude(volume)} [dim]({_magnitude(average)})[/dim]"


def _magnitude(value: Decimal | None) -> str | None:
    """Abbreviate a large figure: 3.81T, 35.2B, 27.0M."""
    if value is None:
        return None
    for size, suffix in _MAGNITUDES:
        if abs(value) >= size:
            return f"{float(value / size):,.2f}{suffix}"
    return f"{float(value):,.0f}"


def _ratio(value: Decimal | None) -> str | None:
    """Render a plain ratio such as a P/E or a beta."""
    return None if value is None else f"{float(value):,.2f}"


def _price_or_none(value: Decimal | None) -> str | None:
    """Render a price, or nothing at all when there is none."""
    return None if value is None else price(value)


def _percent_or_none(value: Decimal | None) -> str | None:
    """Render a ratio as a percentage, or nothing at all when there is none."""
    return None if value is None else percent(value)


# -- PERFORMANCE --------------------------------------------------------------


def _chart_grid(
    moves: Mapping[PriceRange, RangeMove | None],
    room: int,
) -> RichTable:
    """Print every range out as a small chart, as many to a row as `room` can fit."""
    per_row, points = 1, _CHART_MIN_POINTS
    for count in _CHARTS_PER_ROW:
        points = min(_CHART_MAX_POINTS, _points_across(room, count))
        if points >= _CHART_MIN_POINTS or count == 1:
            per_row = count
            break
    grid = RichTable.grid(padding=(0, 1))
    for _ in range(per_row):
        grid.add_column()
    ranges = list(PriceRange)
    for first in range(0, len(ranges), per_row):
        row = ranges[first : first + per_row]
        grid.add_row(*[_chart(name, moves.get(name), points) for name in row])
    return grid


def _chart(price_range: PriceRange, move: RangeMove | None, points: int) -> Panel:
    """Draw one range: its move in the title, where it starts underneath."""
    title = Text(f"{price_range} ", style="bold")
    if move is None:
        title.rstrip()
        return _chart_panel(title, _placeholder("no data", points))
    plus = "+" if move.change > 0 else ""
    title.append_text(Text.from_markup(signed(plus + money(move.change), move.change)))
    title.append(" ")
    title.append_text(Text.from_markup(_move_ratio(move)))
    if len(move.points) <= _ENDS_ONLY:
        # Offline, the day's move stands but there are no bars to draw it with.
        return _chart_panel(title, _placeholder("no bars offline", points))

    rows = area_chart(
        move.points,
        move.anchor,
        width=points,
        height=_CHART_ROWS,
        style=_CHART_STYLE,
        fill=move.fill,
        unicode=UNICODE,
    )
    body = Text("\n").join(rows)
    begins = (
        clock_time(move.start)
        if price_range is PriceRange.HOURS_2
        else long_date(move.start) or ""
    )
    anchored = price(move.anchor)
    body.append("\n")
    body.append(begins, style="dim")
    body.append(" " * max(1, points - len(begins) - len(anchored)))
    body.append(anchored, style="dim")
    return _chart_panel(title, body)


def _placeholder(message: str, points: int) -> Text:
    """Fill a chart's space with a note, so the grid keeps its shape."""
    above = _CHART_ROWS // 2
    body = Text("\n" * above + message.center(points), style="dim")
    body.append("\n" * (_CHART_ROWS - above))
    return body


def _move_ratio(move: RangeMove) -> str:
    """Give a move as a percentage, or as a multiple once it is enormous."""
    multiple = move.multiple
    if multiple is not None and multiple >= _MULTIPLE_FROM:
        return signed(f"×{float(multiple):,.0f}", move.change)
    if move.change_pct is None:
        return ""
    return graded(
        f"{float(move.change_pct) * 100:+,.2f}%",
        move.change_pct,
        RETURN_STRONG_AT,
    )


def _chart_panel(title: Text, body: Text) -> Panel:
    """Frame one chart."""
    return Panel(
        body,
        title=title,
        title_align="left",
        border_style="grey35",
        expand=False,
        padding=(0, 1),
    )


def _points_across(room: int, per_row: int) -> int:
    """How many points each of `per_row` charts gets in `room` columns."""
    return (room - (per_row - 1)) // per_row - _CHART_FRAME


def _grid_width(per_row: int, points: int) -> int:
    """How many columns `per_row` charts of `points` points take."""
    return per_row * (points + _CHART_FRAME) + (per_row - 1)


def _block(name: str, panel: RenderableType) -> Block:
    """Measure a panel for the tiling layout."""
    return Block.create(
        name=name,
        key=name[:1].lower(),
        panel=panel,
        total=0,
        shown=0,
        data_type=name.lower(),
        data=None,
    )


# -- POSITION -----------------------------------------------------------------


def _pool_table(position: SymbolPosition, *, by_account: bool) -> RichTable:
    """Lay out every pool's holding, with the portfolio-wide total below."""
    share = "Acct%" if by_account else "Type%"
    table = RichTable(
        title=f"{position.symbol} held · {position.currency}",
        title_justify="left",
        border_style="bright_blue",
        header_style="bold bright_white",
    )
    headers = (
        "Pool",
        "Units",
        "Avg",
        "Book",
        "Market",
        "Unreal",
        "Unreal%",
        "Realized",
        "Divs",
        "Total",
        "Total%",
        share,
        "Folio%",
    )
    for header in headers:
        table.add_column(
            header,
            justify="left" if header == "Pool" else "right",
            no_wrap=True,
        )

    for line in position.lines:
        cells = _closed_cells(line) if line.holding.closed else _open_cells(line)
        table.add_row(*cells, style="dim" if line.holding.closed else None)

    table.add_section()
    total = position.total
    table.add_row(
        "POOLED",
        *_figures(total, share=None),
        style=GRAND_TOTAL_ROW_STYLE,
    )
    return table


def _pool_cell(line: PoolLine) -> str:
    """Name a pool in its account type's colour, badging the taxable one."""
    account_type = account_type_of(line.view)
    label = line.view.label
    if account_type is AccountType.NON_REGISTERED:
        label = f"{label} [dim]CRA[/dim]"
    tint = ACCOUNT_TYPE_COLORS.get(account_type) if account_type else None
    return style(label, tint)


def _open_cells(line: PoolLine) -> list[str]:
    """Render an open position in one pool."""
    return [
        _pool_cell(line),
        *_figures(line.holding, share=line.holding.weight_in_pool),
    ]


def _figures(holding: Holding, *, share: Decimal | None) -> list[str]:
    """Render a holding's figures, everything after the pool's name."""
    return [
        units(holding.units),
        price(holding.avg_cost),
        money(holding.book_value),
        money(holding.market_value),
        signed(money(holding.unrealized), holding.unrealized),
        lifetime_return(holding.unrealized_pct),
        signed(money(holding.realized, blank_zero=True), holding.realized),
        income(holding.dividends),
        signed(money(holding.total_pnl), holding.total_pnl),
        lifetime_return(holding.total_pnl_pct),
        weight(share) if share is not None else "",
        weight(holding.weight_in_folio),
    ]


def _closed_cells(line: PoolLine) -> list[str]:
    """Render a pool that sold out: only what it realized and earned remains."""
    holding = line.holding
    total = holding.closed_total
    return [
        f"{line.view.label} (closed)",
        "",
        "",
        "",
        "",
        "",
        "",
        signed(money(holding.realized, blank_zero=True), holding.realized),
        income(holding.dividends),
        signed(money(total, blank_zero=True), total),
        "",
        "",
        "",
    ]


def _conversion_notes(position: SymbolPosition, quote: Quote) -> list[str]:
    """Say what a table in another currency than the quote's was converted at."""
    if position.currency is quote.currency or position.fx_rate is None:
        return []
    rate = f"USDCAD {float(position.fx_rate):,.4f} ({position.fx_date})"
    note = (
        "[dim]Cost base at each transaction's settle-date rate; "
        f"market value at {rate}.[/dim]"
    )
    return [note]


def _footer(position: SymbolPosition) -> list[str]:
    """Say when it was first traded, and anything the replay flagged."""
    notes: list[str] = []
    held = position.held_for(datetime.now(TORONTO_TZ).date())
    years, months = held
    notes.append(
        f"[dim]First traded {long_date(position.first_traded)} · "
        f"held {years}y {months}m[/dim]",
    )
    if not position.total.priced and not position.total.closed:
        notes.append(
            f"[yellow]{WARN_GLYPH} No live price: market value and unrealized "
            f"gain are blank.[/yellow]",
        )
    if position.flags:
        codes = ", ".join(str(code) for code in position.flags)
        notes.append(
            f"[yellow]{WARN_GLYPH} Flags: {codes}. Run `folio check`.[/yellow]",
        )
    return notes


# -- MATRIX -------------------------------------------------------------------


def show_matrix(
    rows: Sequence[PerformanceRow],
    *,
    title: str,
    pool_weight: bool,
    badge: str | None = None,
) -> None:
    """Print securities one per row, with their move over every range.

    Reading down a range's column compares them over that range: the best and
    worst of each column are drawn bold.

    Args:
        rows: The securities, in the order to list them.
        title: The pool the market values and weights are read from.
        pool_weight: Show each security's share of that pool as well as of the
            whole portfolio. Only worth a column when the pool is not the
            whole portfolio.
        badge: Cache-freshness line, printed above the table.
    """
    if badge:
        console_print(badge)
    table = RichTable(
        title=f"Performance · {title} · market value in CAD",
        title_justify="left",
        border_style="bright_blue",
        header_style="bold bright_white",
    )
    table.add_column("Symbol", no_wrap=True)
    for header in ("Last", "Market", *(("Wt%",) if pool_weight else ()), "Folio%"):
        table.add_column(header, justify="right", no_wrap=True)
    for price_range in PriceRange:
        table.add_column(str(price_range), justify="right", no_wrap=True)

    extremes = {price_range: _extremes(rows, price_range) for price_range in PriceRange}
    for row in rows:
        quote = row.quote
        last = price(quote.price)
        if quote.currency is not None and quote.price is not None:
            last = currency_tint(quote.currency, last)
        cells = [
            row.symbol,
            last,
            money(row.market) if row.market is not None else "",
            *((weight(row.weight_in_pool),) if pool_weight else ()),
            weight(row.weight_in_folio) if row.weight_in_folio is not None else "",
        ]
        for price_range in PriceRange:
            move = row.moves.get(price_range)
            cell = EM_DASH if move is None else _move_ratio(move)
            if move is not None and move.change_pct in extremes[price_range]:
                cell = f"[bold]{cell}[/bold]"
            cells.append(cell)
        table.add_row(*cells)

    fitted = fit(table, _MATRIX_DROP_ORDER)
    if fitted.note:
        console_print(fitted.note)
    console_print(fitted.table)


def _extremes(rows: Sequence[PerformanceRow], price_range: PriceRange) -> set[Decimal]:
    """Find the best and worst move over one range, when there are two to compare."""
    moves = [
        move.change_pct
        for row in rows
        if (move := row.moves.get(price_range)) is not None
        and move.change_pct is not None
    ]
    if len(moves) < _COMPARABLE:
        return set()
    return {max(moves), min(moves)}
