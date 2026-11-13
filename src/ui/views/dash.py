"""Render logic for the dashboard related views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table as RichTable

from domain import Currency, Grain
from domain.numeric import ZERO, q2
from engine.pools import (
    Conversion,
    Converted,
    TotalReturnHidden,
    UnitsSuspect,
    Unpriced,
)
from exporters.sheet_spec import Fmt
from term import console_print
from ui.cells import (
    DELTA_GLYPH,
    UNICODE,
    WARN_GLYPH,
    currency_tint,
    day_move,
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
from ui.layout.bars import hbar, meter
from ui.layout.columns import FormattedColumn, add_column
from ui.layout.fit import fit
from ui.layout.tiles import Tile, TilingLayout
from ui.vocabulary import (
    ACCOUNT_TYPE_COLORS,
    FLOW_COLORS,
    GRAND_TOTAL_ROW_STYLE,
    LOSS,
    REFERENCE_STYLE,
    ROOM_BAR,
    ROOM_BAR_ASCII,
    ROOM_FULL,
    ROOM_LOW,
    ROOM_LOW_BELOW,
    ROOM_OVER,
    ROOM_PARTIAL,
    SUBTOTAL_ROW_STYLE,
    TABLE_BORDER,
    TABLE_HEADER,
    WEIGHT_BAR_FULL,
    WEIGHT_BAR_STYLE,
    WEIGHT_BAR_WIDTH,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from decimal import Decimal

    from rich.console import RenderableType

    from engine.flows import Flows, Room
    from engine.holdings import ClosedTotals, CurrencyTotals, Holding, HoldingSet
    from engine.pools import Disclosure, Return, ValuedPool
    from engine.since_update import SinceUpdate
    from ui.layout.fit import FitResult


@dataclass(frozen=True)
class _HoldingColumn:
    """How one dashboard column is rendered.

    Attributes:
        column: The table column it fills.
        cell: Reads the rendered string for one holding.
        wide_only: Shown only when `--wide` asked for it.
        muted: Reference data rather than performance, so drawn quieter.
    """

    column: FormattedColumn
    cell: Callable[[Holding], str]
    wide_only: bool = False
    muted: bool = False

    @property
    def header(self) -> str:
        """The heading it was given, before any shortening."""
        return self.column.key

    def render(self, holding: Holding) -> str:
        """Render this column's cell for one holding row."""
        text = self.cell(holding)
        return style(text, REFERENCE_STYLE) if self.muted else text


def _figure(
    header: str,
    cell: Callable[[Holding], str],
    fmt: Fmt,
    *,
    coarse: bool = False,
    muted: bool = False,
) -> _HoldingColumn:
    """Describe a right-aligned column of one figure per holding."""
    column = FormattedColumn(
        header,
        fmt=fmt,
        coarse=coarse,
        justify="right",
        no_wrap=True,
    )
    return _HoldingColumn(column, cell, muted=muted)


def _weight_bar(holding: Holding) -> str:
    """Draw a holding's weight in its pool as a bar, full at `WEIGHT_BAR_FULL`."""
    weight = holding.weight_in_pool
    if weight is None:
        return ""
    bar = hbar(weight / WEIGHT_BAR_FULL, WEIGHT_BAR_WIDTH, unicode=UNICODE)
    return style(bar, WEIGHT_BAR_STYLE)


def _symbol_cell(holding: Holding) -> str:
    """Render the symbol, badged when its units are known to be wrong."""
    if holding.units_suspect:
        return f"[red]{holding.symbol} {WARN_GLYPH}[/red]"
    return holding.symbol


# Every column the dashboard can show, in display order. `--wide` adds the ones
# marked `wide_only`; everything else is always offered, then conceded by
# `fit_table` if the terminal is too narrow to hold it.
_HOLDING_COLUMNS: tuple[_HoldingColumn, ...] = (
    _HoldingColumn(FormattedColumn("Symbol", no_wrap=True), _symbol_cell),
    _HoldingColumn(FormattedColumn("Name"), lambda h: h.name or "", wide_only=True),
    _figure("Units", lambda h: units(h.units), Fmt.UNITS, muted=True),
    _figure("Avg", lambda h: price(h.avg_cost), Fmt.PRICE, muted=True),
    _figure("Last", lambda h: price(h.price), Fmt.PRICE),
    _figure("Change", lambda h: signed(price(h.change), h.change), Fmt.PRICE_SIGNED),
    # A day's move keeps both decimals: its second one is often all of it.
    _figure("Change%", lambda h: day_move(h.change_pct), Fmt.PERCENT_SIGNED),
    _figure("PnL", lambda h: signed(money(h.day_pnl), h.day_pnl), Fmt.MONEY_SIGNED),
    _figure("PnL%", lambda h: day_move(h.day_pnl_pct), Fmt.PERCENT_SIGNED),
    _figure(
        "Unreal",
        lambda h: signed(money(h.unrealized), h.unrealized),
        Fmt.MONEY_SIGNED,
    ),
    _figure(
        "Unreal%",
        lambda h: lifetime_return(h.unrealized_pct),
        Fmt.PERCENT_SIGNED,
        coarse=True,
    ),
    # Realized and Divs blank their zeros
    _figure(
        "Realized",
        lambda h: signed(money(h.realized, blank_zero=True), h.realized),
        Fmt.MONEY_SIGNED,
    ),
    _figure("Divs", lambda h: income(h.dividends), Fmt.MONEY),
    _figure(
        "Total",
        lambda h: signed(money(h.total_pnl), h.total_pnl),
        Fmt.MONEY_SIGNED,
    ),
    _figure(
        "Total%",
        lambda h: lifetime_return(h.total_pnl_pct),
        Fmt.PERCENT_SIGNED,
        coarse=True,
    ),
    _figure("Book", lambda h: money(h.book_value), Fmt.MONEY, muted=True),
    _figure("Market", lambda h: money(h.market_value), Fmt.MONEY),
    _figure("Wt%", lambda h: weight(h.weight_in_pool), Fmt.PERCENT, coarse=True),
    # The bar has a column of its own so every `Wt%` figure, totals included,
    # stays right-aligned. Totals rows leave it blank.
    _HoldingColumn(
        FormattedColumn("Weight", no_wrap=True),
        _weight_bar,
        wide_only=True,
    ),
    _figure("Folio%", lambda h: weight(h.weight_in_folio), Fmt.PERCENT, coarse=True),
)

# Column drop order as terminals get too narrow.
_DASH_DROP_ORDER = (
    "Weight",
    "Name",
    "Book",
    "Folio%",
    "Change",
    "Unreal%",
    "PnL",
    "Unreal",
    "Realized",
    "Divs",
)


def _columns(holdings: HoldingSet, *, wide: bool) -> tuple[_HoldingColumn, ...]:
    """Choose which columns this table offers before any conceding."""
    # `Folio%` is only worth a column when the table is part of the folio rather
    # than all of it, since it is otherwise the same number as `Wt%`. A narrowed
    # grain is the usual way that happens, and `-c USD` is the other: it hides
    # the CAD holdings, so even the folio-wide table is then a part.
    partial = holdings.grain is not Grain.FOLIO or holdings.excluded > 0
    return tuple(
        spec
        for spec in _HOLDING_COLUMNS
        if (wide or not spec.wide_only) and (partial or spec.header != "Folio%")
    )


def holdings_table(
    valued: ValuedPool,
    title: str | None = None,
    *,
    wide: bool = False,
    show_closed: bool = False,
) -> FitResult:
    """Render the holdings as a Rich table, fitted to the terminal.

    Rows are grouped by the currency they trade in, each having its own subtotal.
    Since the values are currency-denominated, the grouping is mandatory in order
    for sensible sorting.

    A currency group that has any closed holdings gets one more aggregated row at the
    bottom. You can see them all when `show_closed` is True.

    Args:
        valued: The pool whose holdings to show.
        title: Table title.
        wide: Offer the wide-only columns as well.
        show_closed: Break the aggregate "Closed" row open into one row per
            closed holding instead.

    Returns:
        The fitted table alongside the columns it had to give up, so the
        caller can say what is missing.
    """
    holdings = valued.holdings
    specs = _columns(holdings, wide=wide)
    table = RichTable(
        title=title,
        show_header=True,
        header_style=TABLE_HEADER,
        border_style=TABLE_BORDER,
    )
    for spec in specs:
        add_column(table, spec.column)

    for group in holdings.by_currency:
        for holding in group.members:
            table.add_row(*[spec.render(holding) for spec in specs])

        if group.closed_members:
            if show_closed:
                for holding in group.closed_members:
                    table.add_row(*_closed_cells(holding, specs))
            else:
                table.add_row(*_closed_summary_cells(group.closed_totals, specs))

        # A rule above each total says "this row is a different kind of thing"
        # in one line rather than a blank one, keeping the table short.
        table.add_section()
        table.add_row(
            *_subtotal_cells(group, specs, valued.subtotal_return(group)),
            style=SUBTOTAL_ROW_STYLE,
        )
        table.add_section()

    # For multiple currencies, show an additional grand-total row.
    if holdings.mixed_currency:
        table.add_row(
            *_grand_total_cells(valued, specs),
            style=GRAND_TOTAL_ROW_STYLE,
        )

    return fit(table, _DASH_DROP_ORDER)


def _closed_cells(holding: Holding, specs: Sequence[_HoldingColumn]) -> list[str]:
    """Render one closed holding, blanking every column that no longer applies."""
    total = holding.closed_total
    cells = {
        "Symbol": f"{holding.symbol} [dim](closed)[/dim]",
        "Realized": signed(
            money(holding.realized, blank_zero=True),
            holding.realized,
        ),
        "Divs": income(holding.dividends),
        "Total": signed(money(total, blank_zero=True), total),
    }
    return [cells.get(spec.header, "") for spec in specs]


def _closed_summary_cells(
    totals: ClosedTotals,
    specs: Sequence[_HoldingColumn],
) -> list[str]:
    """Aggregate every closed holding in one currency group into one row."""
    total = totals.total
    cells = {
        "Symbol": f"[bold dim]Closed ({totals.count})[/bold dim]",
        "Realized": signed(money(totals.realized, blank_zero=True), totals.realized),
        "Divs": income(totals.dividends),
        "Total": signed(money(total, blank_zero=True), total),
    }
    return [cells.get(spec.header, "") for spec in specs]


def _subtotal_cells(
    group: CurrencyTotals,
    specs: Sequence[_HoldingColumn],
    total: Return,
) -> list[str]:
    """Subtotal one currency group, in that group's own currency.

    Every figure here is read off `group`, which the engine already totalled.

    Args:
        group: The currency group's totals.
        specs: The columns this table is rendering.
        total: What the `Total%` cell says, from `ValuedPool.subtotal_return`.

    Returns:
        One cell per column in `specs`.
    """
    cells = {
        "Symbol": f"[bold]{group.count} held[/bold]",
        # A totals row has no unit count, and the column is never conceded, so
        # it is free space in exactly the place the currency belongs.
        "Units": currency_tint(group.currency, str(group.currency)),
        "Book": money(group.book),
        "Market": money(group.market),
        "PnL": signed(money(group.day_pnl), group.day_pnl),
        "PnL%": day_move(group.day_pnl_pct),
        "Unreal": signed(money(group.unrealized), group.unrealized),
        "Unreal%": lifetime_return(group.unrealized_pct, group.unrealized),
        "Realized": signed(
            money(group.realized, blank_zero=True),
            group.realized,
        ),
        "Divs": income(group.dividends),
        "Total": signed(money(group.total_pnl), group.total_pnl),
        "Total%": lifetime_return(total.ratio, total.gain),
        "Wt%": percent(group.weight_in_pool),
        "Folio%": percent(group.weight_in_folio),
    }
    return [cells.get(spec.header, "") for spec in specs]


def _grand_total_cells(
    valued: ValuedPool,
    specs: Sequence[_HoldingColumn],
) -> list[str]:
    """Total across every currency, converted into the totals currency."""
    holdings = valued.holdings
    totals = holdings.totals_currency
    total = valued.total_return
    cells = {
        "Symbol": "[bold]Total[/bold]",
        "Units": currency_tint(totals, f"({totals})"),
        "Wt%": percent(holdings.total_weight_in_pool),
        # How much of everything owned this whole grain accounts for
        "Folio%": percent(holdings.total_weight_in_folio),
        "Book": money(holdings.total_book),
        "Market": money(holdings.total_market),
        "PnL": signed(money(holdings.total_day_pnl), holdings.total_day_pnl),
        "Unreal": signed(money(holdings.total_unrealized), holdings.total_unrealized),
        "Realized": signed(
            money(holdings.total_realized, blank_zero=True),
            holdings.total_realized,
        ),
        "Divs": income(holdings.total_dividends),
        "Total": signed(money(holdings.total_pnl), holdings.total_pnl),
        "PnL%": day_move(holdings.total_day_pnl_pct, holdings.total_day_pnl),
        "Unreal%": lifetime_return(
            holdings.total_unrealized_pct,
            holdings.total_unrealized,
        ),
        "Total%": lifetime_return(total.ratio, total.gain),
    }
    return [cells.get(spec.header, "") for spec in specs]


def flows_panel(valued: ValuedPool) -> Panel:
    """Render cash, contributions and cumulative income for one pool.

    Args:
        valued: The pool, valued. Its label titles the panel.

    Returns:
        A Rich panel. Cash is listed per currency and never summed across them:
        a blended total would hide an FX assumption and its date.
    """
    # A borderless table rather than a grid, so `add_section` can draw a faint
    # rule between capital, income and room. The one-space divider plus one
    # collapsed space of padding keeps the grid's two-space gap, and its width.
    flows = valued.flows
    table = RichTable(
        box=box.HORIZONTALS,
        show_header=False,
        show_edge=False,
        padding=(0, 1),
        collapse_padding=True,
        pad_edge=False,
        border_style="dim",
    )
    table.add_column(justify="left", style="bold")
    table.add_column(justify="right")
    table.add_column(justify="left")

    _add_money_rows(table, flows)

    if flows.room is not None:
        table.add_section()
        for row in _room_rows(flows.room):
            table.add_row(*row)

    body: list[RenderableType] = [table]
    body.extend(_cash_alerts(flows))

    return Panel(
        Group(*body),
        title=_panel_title(valued),
        border_style="red" if flows.negative_currencies else TABLE_BORDER,
        expand=False,
    )


def _panel_title(valued: ValuedPool) -> str:
    """Name the panel, accented by the pool's account type when it has one."""
    accent = _accent(valued.flows)
    return style(valued.label, f"bold {accent}") if accent else valued.label


def _accent(flows: Flows) -> str | None:
    """Pick the colour of the pool's account type, if it has one."""
    if flows.account_type is None:
        return None
    return ACCOUNT_TYPE_COLORS.get(flows.account_type)


# The first measure past capital, where the income section begins.
_INCOME_START = "Dividends"


def _add_money_rows(table: RichTable, flows: Flows) -> None:
    """Add one labelled row per measure, one line per currency it holds."""
    measures = (
        ("Cash", flows.cash, True),
        ("Contributions", flows.contributions, False),
        ("Withdrawn", flows.withdrawals, False),
        ("Transferred", flows.transfers, False),
        ("Net Deposits", flows.net_deposits, False),
        ("Dividends", flows.dividends, False),
        ("Fees", flows.fees, False),
        ("Realized", flows.realized, False),
    )
    for label, amounts, alert in measures:
        # A no-op until a row exists, so an empty capital group draws no rule.
        if label == _INCOME_START:
            table.add_section()
        if not amounts:
            continue
        transfers = amounts is flows.transfers
        for index, currency in enumerate(sorted(amounts, key=str)):
            amount = amounts[currency]
            note = currency_tint(currency, str(currency))
            moved = flows.moved_by_transfers(currency) if transfers else None
            if moved is not None:
                note += style(f"  of {money(moved)} moved", "dim")
            table.add_row(
                label if index == 0 else "",
                _flow_amount(label, amount, alert=alert),
                note,
            )


def _flow_amount(label: str, amount: Decimal, *, alert: bool) -> str:
    """Colour one flows figure by what it means for the pool.

    Args:
        label: The measure's row label, which picks its colour.
        amount: The figure.
        alert: Colour only when negative, since that signals a problem.

    Returns:
        The rendered amount.
    """
    text = money(amount)
    if alert:
        return style(text, LOSS) if q2(amount) < ZERO else text
    if label == "Realized":
        return signed(text, q2(amount))
    return style(text, FLOW_COLORS.get(label))


def _room_rows(room: Room) -> list[tuple[str, str, str]]:
    """Render the contribution-room lines for a registered pool.

    With a limit, a gauge follows on its own line, exactly as wide as the
    figures above it, so the panel grows a row rather than a column.
    """
    label = f"Room {room.year}"
    used = money(room.used)
    remaining = room.remaining
    if room.limit is None or remaining is None:
        return [(label, used, style("contributed, no limit configured", "dim"))]
    figures = f"{used} / {money(room.limit)}"
    colour = _room_colour(room)
    if room.full:
        note = "full"
    elif room.over:
        note = f"{money(abs(remaining))} over"
    else:
        note = f"{money(remaining)} left"
    rows = [(label, style(figures, colour), style(note, "dim"))]
    if room.used_ratio is not None:
        glyphs = ROOM_BAR if UNICODE else ROOM_BAR_ASCII
        gauge = meter(room.used_ratio, len(figures), glyphs)
        rows.append(("", style(gauge, colour), ""))
    return rows


def _room_colour(room: Room) -> str:
    """Pick the room colour: under half used, partly used, full, or over."""
    if room.full:
        return ROOM_FULL
    if room.over:
        return ROOM_OVER
    ratio = room.used_ratio
    if ratio is not None and ratio < ROOM_LOW_BELOW:
        return ROOM_LOW
    return ROOM_PARTIAL


def _cash_alerts(flows: Flows) -> list[RenderableType]:
    """Say what negative cash means, rather than just showing the number.

    This is the most useful thing the dashboard points out: it needs no brokerage
    balance to detect, and it catches a missing transaction immediately.
    """
    negative = flows.negative_currencies
    if not negative:
        return []
    currencies = ", ".join(str(currency) for currency in negative)
    return [
        "",
        (
            f"[red]{WARN_GLYPH} Cash is negative ({currencies}). A transaction is "
            f"probably missing.[/red]\n[dim]  Run `folio check` to find it.[/dim]"
        ),
    ]


def show_by_type(
    valued_pools: Sequence[ValuedPool],
    *,
    wide: bool = False,
    show_closed: bool = False,
    badge: str | None = None,
    since: SinceUpdate | None = None,
) -> None:
    """Print one dashboard per account type, flows tiled above the holdings.

    The flows panels are narrow and similar in height, so they tile into a
    strip across the top. The holdings tables are each nearly as wide as the
    terminal, so they could never sit side by side and follow in full width,
    each under a heading of its own.

    Args:
        valued_pools: Each account type's pool, valued.
        wide: Offer the wide-only columns.
        show_closed: Break the aggregate "Closed" row open per holding.
        badge: Cache-freshness line, printed above everything.
        since: What changed in the whole folio since the latest update.
    """
    _print_header(badge, since)

    by_tile = {Tile(flows_panel(valued)): valued for valued in valued_pools}
    layout = TilingLayout(list(by_tile))
    layout.render()

    # The tiling reorders the panels, so the sections follow its reading order
    # rather than the given one. Split from its flows panel, each table needs a
    # heading to say whose it is.
    for tile in layout.reading_order:
        valued = by_tile[tile]
        console_print("")
        console_print(
            Rule(
                _panel_title(valued),
                align="left",
                style=_accent(valued.flows) or "",
            ),
        )
        _print_holdings(valued, wide=wide, show_closed=show_closed)


def show_dashboard(
    valued: ValuedPool,
    *,
    wide: bool = False,
    show_closed: bool = False,
    badge: str | None = None,
    since: SinceUpdate | None = None,
) -> None:
    """Print one pool's dashboard: badge, flows panel, then holdings.

    Args:
        valued: The pool, valued. Its label heads both the panel and the table.
        wide: Offer the wide-only columns.
        show_closed: Break the aggregate "Closed" row open per holding.
        badge: Cache-freshness line, printed above everything.
        since: What changed in the pool since the latest update.
    """
    _print_header(badge, since)
    console_print(flows_panel(valued))
    _print_holdings(valued, wide=wide, show_closed=show_closed)


def _print_header(badge: str | None, since: SinceUpdate | None) -> None:
    """Print the freshness line, then what changed since the latest update."""
    if badge:
        console_print(badge)
    if since is not None:
        console_print(_since_line(since))


_SINCE_SEPARATOR = style(" · " if UNICODE else " | ", "dim")


def _since_line(since: SinceUpdate) -> str:
    """Word what changed since the latest update on one line, in CAD.

    One line whatever the scope, so the flows panels keep their height. A figure
    that did not move is left out.
    """
    parts = []
    if since.growth:
        growth = signed(money(since.growth, plus=True), since.growth)
        parts.append(f"{style('growth', 'dim')} {growth}")
    for label, amount in (
        ("deposits", since.net_deposits),
        ("dividends", since.dividends),
    ):
        if amount:
            parts.append(f"{style(label, 'dim')} {money(amount, plus=True)}")
    if since.new_txns:
        plural = "" if since.new_txns == 1 else "s"
        parts.append(f"+{since.new_txns} txn{plural}")
    saved = style(f"since {long_date(since.saved_on)}", "dim")
    figures = (
        f"{_SINCE_SEPARATOR.join(parts)} {style('(CAD)', 'dim')}"
        if parts
        else style("no change", "dim")
    )
    return f"[cyan]{DELTA_GLYPH}[/cyan] {saved}{_SINCE_SEPARATOR}{figures}"


def _print_holdings(
    valued: ValuedPool,
    *,
    wide: bool,
    show_closed: bool,
) -> None:
    """Print one pool's holdings table with everything it has to disclose.

    Args:
        valued: The pool, valued.
        wide: Offer the wide-only columns.
        show_closed: Break the aggregate "Closed" row open per holding.
    """
    if not valued.occupied:
        console_print("[yellow]No open holdings in this pool.[/yellow]")
        return

    fitted = holdings_table(valued, wide=wide, show_closed=show_closed)
    if fitted.note:
        console_print(fitted.note)
    console_print(fitted.table)
    for disclosure in valued.disclosures:
        console_print(_disclosure_line(disclosure))


def _disclosure_line(disclosure: Disclosure) -> str:
    """Word one disclosure as the line printed under the table."""
    match disclosure:
        case Unpriced(symbols):
            return (
                f"[yellow]{WARN_GLYPH} {len(symbols)} holding(s) unpriced and "
                f"excluded from totals: {', '.join(symbols)}[/yellow]"
            )
        case TotalReturnHidden(excluded, shown):
            return (
                f"[dim]Total% is blank: it measures the pool against its net "
                f"deposits, and {excluded} CAD holding(s) are hidden by "
                f"--currency {shown}. Drop the flag to see it.[/dim]"
            )
        case Converted(what, into, rate, rate_date):
            return (
                f"[dim]{_converted_what(what, into)} at USDCAD {float(rate):,.4f} "
                f"({rate_date}). Cost base keeps its own historical rates.[/dim]"
            )
        case UnitsSuspect(codes):
            listed = ", ".join(str(code) for code in codes)
            return (
                f"[red]{WARN_GLYPH} Badged holdings carry {listed}; their units "
                f"are known to be wrong. Run `folio check`.[/red]"
            )


def _converted_what(what: Conversion, into: Currency) -> str:
    """Say which figures a conversion produced, as the line opens."""
    if what is Conversion.TOTAL:
        return f"Total ({into}) converted"
    if what is Conversion.TOTAL_RETURN:
        return f"Total% ({into}) converted"
    return "Valued"
