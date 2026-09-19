"""Render logic for the dashboard related views."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table as RichTable

from domain import Currency, WarningCode
from domain.numeric import ZERO, q2
from engine.positions import summarize_closed
from term import console_print, supports_unicode
from ui.layout.bars import hbar, meter
from ui.layout.fit import fit, fit_table
from ui.layout.tiles import Block, TilingLayout
from ui.vocabulary import (
    ACCOUNT_TYPE_COLORS,
    CURRENCY_COLORS,
    DAY_MOVE_STRONG_AT,
    FLAT,
    FLAT_BELOW,
    FLOW_COLORS,
    GAIN,
    GAIN_STRONG,
    GRAND_TOTAL_ROW_STYLE,
    INCOME,
    LOSS,
    LOSS_STRONG,
    MONEY_PRECISION,
    PRICE_PRECISION,
    REFERENCE_STYLE,
    RETURN_STRONG_AT,
    ROOM_BAR,
    ROOM_BAR_ASCII,
    ROOM_FULL,
    ROOM_LOW,
    ROOM_LOW_BELOW,
    ROOM_OVER,
    ROOM_PARTIAL,
    SUBTOTAL_ROW_STYLE,
    UNIT_PRECISION,
    WEIGHT_ALERT,
    WEIGHT_ALERT_AT,
    WEIGHT_BAR_FULL,
    WEIGHT_BAR_STYLE,
    WEIGHT_BAR_WIDTH,
    WEIGHT_WARN,
    WEIGHT_WARN_AT,
)

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Sequence

    from rich.console import RenderableType
    from rich.table import JustifyMethod

    from engine.flows import Flows, Room
    from engine.positions import CurrencyTotals, Holding, HoldingSet
    from services.quotes_service import Quote
    from ui.layout.fit import FitResult

_UNICODE = supports_unicode()
_WARN_GLYPH = "⚠" if _UNICODE else "!"
_EM_DASH = "—" if _UNICODE else "-"

_PERCENT_PRECISION = 2

# Diagnostics that make a pool's units untrustworthy.
_UNITS_SUSPECT = frozenset(
    {
        WarningCode.OVERSELL,
        WarningCode.NEGATIVE_FINAL_POSITION,
    },
)


@dataclass(frozen=True)
class _ColumnSpec:
    """How one dashboard column is rendered.

    Attributes:
        header: Column heading.
        cell: Reads the rendered string for one holding.
        justify: Alignment.
        wide_only: Shown only when `--wide` asked for it.
        muted: Reference data rather than performance, so drawn quieter.
    """

    header: str
    cell: Callable[[Holding], str]
    justify: JustifyMethod = "right"
    wide_only: bool = False
    muted: bool = False

    def render(self, holding: Holding) -> str:
        """Render this column's cell for one position row."""
        text = self.cell(holding)
        return _style(text, REFERENCE_STYLE) if self.muted else text


def _money(value: Decimal | None, *, blank_zero: bool = False) -> str:
    """Render a money figure, leaving a genuine blank blank.

    Rounded to cents *before* the sign is read, so an FX residue of a
    millionth of a cent prints as `0.00` rather than the alarming `-0.00`.
    """
    if value is None:
        return _EM_DASH
    number = float(q2(value)) + 0.0  # collapses -0.0, which reads as a real debit
    if blank_zero and number == 0:
        return ""
    return f"{number:,.{MONEY_PRECISION}f}"


def _price(value: Decimal | None) -> str:
    """Render a per-unit price at its own, finer precision."""
    if value is None:
        return _EM_DASH
    return f"{float(value):,.{PRICE_PRECISION}f}".rstrip("0").rstrip(".")


def _units(value: Decimal | None) -> str:
    """Render a share count, dropping the zeros a whole position does not need."""
    if value is None:
        return _EM_DASH
    text = f"{float(value):,.{UNIT_PRECISION}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _percent(value: Decimal | None) -> str:
    """Render a ratio as a percentage."""
    if value is None:
        return _EM_DASH
    return f"{float(value) * 100:,.{_PERCENT_PRECISION}f}%"


def _style(text: str, style: str | None) -> str:
    """Wrap a rendered cell in a style, leaving blanks and placeholders bare.

    A blank must stay truly blank, since `fit` drops a column no row fills in.
    """
    if not text or not style or text == _EM_DASH:
        return text
    return f"[{style}]{text}[/]"


def _signed(text: str, value: Decimal | None) -> str:
    """Colour a rendered cell by the sign of the number behind it.

    Green up, red down, matching every other table in the app.
    """
    if value is None or value == 0:
        return text
    return _style(text, GAIN if value > 0 else LOSS)


def _graded(
    text: str,
    ratio: Decimal | None,
    strong_at: Decimal,
    sign: Decimal | None = None,
) -> str:
    """Colour a percentage by its sign and how large it is.

    A move too small to matter is dimmed, an ordinary one takes the plain sign
    colour, and one at or beyond `strong_at` is drawn bold and bright.

    Args:
        text: The rendered percentage.
        ratio: The ratio behind it, which decides the intensity.
        strong_at: The magnitude at which the colour turns strong.
        sign: What decides up or down, when that is not the ratio itself.

    Returns:
        The cell, marked up.
    """
    if ratio is not None and abs(ratio) < FLAT_BELOW:
        return _style(text, FLAT)
    sign = ratio if sign is None else sign
    if ratio is None or sign is None or sign == 0:
        return text
    strong = abs(ratio) >= strong_at
    if sign > 0:
        return _style(text, GAIN_STRONG if strong else GAIN)
    return _style(text, LOSS_STRONG if strong else LOSS)


def _day_move(value: Decimal | None, sign: Decimal | None = None) -> str:
    """Render a one-day percentage move, graded by its size."""
    return _graded(_percent(value), value, DAY_MOVE_STRONG_AT, sign)


def _return(value: Decimal | None, sign: Decimal | None = None) -> str:
    """Render a lifetime percentage return, graded by its size."""
    return _graded(_percent(value), value, RETURN_STRONG_AT, sign)


def _income(value: Decimal) -> str:
    """Render dividend income in its own colour, blanking a zero."""
    return _style(_money(value, blank_zero=True), INCOME)


def _currency(currency: Currency, text: str) -> str:
    """Tint a currency label with that currency's colour."""
    return _style(text, CURRENCY_COLORS.get(currency, "dim"))


def _weight(value: Decimal | None) -> str:
    """Render a position's weight, flagged once it concentrates its pool."""
    text = _percent(value)
    if value is None:
        return text
    if value >= WEIGHT_ALERT_AT:
        return _style(text, WEIGHT_ALERT)
    if value >= WEIGHT_WARN_AT:
        return _style(text, WEIGHT_WARN)
    return text


def _weight_bar(holding: Holding) -> str:
    """Draw a position's weight in its pool as a bar, full at `WEIGHT_BAR_FULL`."""
    weight = holding.weight_in_pool
    if weight is None:
        return ""
    bar = hbar(weight / WEIGHT_BAR_FULL, WEIGHT_BAR_WIDTH, unicode=_UNICODE)
    return _style(bar, WEIGHT_BAR_STYLE)


def _symbol_cell(holding: Holding) -> str:
    """Render the symbol, badged when its units are known to be wrong."""
    if set(holding.flags) & _UNITS_SUSPECT:
        return f"[red]{holding.symbol} {_WARN_GLYPH}[/red]"
    return holding.symbol


# Every column the dashboard can show, in display order. `--wide` adds the ones
# marked `wide_only`; everything else is always offered, then conceded by
# `fit_table` if the terminal is too narrow to hold it.
_HOLDING_COLUMNS: tuple[_ColumnSpec, ...] = (
    _ColumnSpec("Symbol", _symbol_cell, justify="left"),
    _ColumnSpec(
        "Name",
        lambda h: h.name or "",
        justify="left",
        wide_only=True,
    ),
    _ColumnSpec("Units", lambda h: _units(h.units), muted=True),
    _ColumnSpec("Avg", lambda h: _price(h.avg_cost), muted=True),
    _ColumnSpec("Last", lambda h: _price(h.price)),
    _ColumnSpec("Change", lambda h: _signed(_price(h.change), h.change)),
    _ColumnSpec("Change%", lambda h: _day_move(h.change_pct)),
    _ColumnSpec("PnL", lambda h: _signed(_money(h.day_pnl), h.day_pnl)),
    _ColumnSpec("PnL%", lambda h: _day_move(h.day_pnl_pct)),
    _ColumnSpec("Unreal", lambda h: _signed(_money(h.unrealized), h.unrealized)),
    _ColumnSpec("Unreal%", lambda h: _return(h.unrealized_pct)),
    # Realized and Divs blank their zeros
    _ColumnSpec(
        "Realized",
        lambda h: _signed(_money(h.realized, blank_zero=True), h.realized),
    ),
    _ColumnSpec("Divs", lambda h: _income(h.dividends)),
    _ColumnSpec("Total", lambda h: _signed(_money(h.total_pnl), h.total_pnl)),
    _ColumnSpec("Total%", lambda h: _return(h.total_pnl_pct)),
    _ColumnSpec("Book", lambda h: _money(h.book_value), muted=True),
    _ColumnSpec("Market", lambda h: _money(h.market_value)),
    _ColumnSpec("Wt%", lambda h: _weight(h.weight_in_pool)),
    # The bar has a column of its own so every `Wt%` figure, totals included,
    # stays right-aligned. Totals rows leave it blank.
    _ColumnSpec("Weight", _weight_bar, justify="left", wide_only=True),
    _ColumnSpec("Folio%", lambda h: _weight(h.weight_in_folio)),
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


def _columns(holdings: HoldingSet, *, wide: bool) -> tuple[_ColumnSpec, ...]:
    """Choose which columns this table offers before any conceding."""
    # `Folio%` is only worth a column when the table is part of the folio rather
    # than all of it, since it is otherwise the same number as `Wt%`. A narrowed
    # scope is the usual way that happens, and `-c USD` is the other: it hides
    # the CAD holdings, so even the portfolio-wide table is then a part.
    partial = holdings.scope.name != "FOLIO" or holdings.excluded > 0
    return tuple(
        spec
        for spec in _HOLDING_COLUMNS
        if (wide or not spec.wide_only) and (partial or spec.header != "Folio%")
    )


def holdings_table(
    holdings: HoldingSet,
    title: str | None = None,
    *,
    flows: Flows | None = None,
    wide: bool = False,
    show_closed: bool = False,
) -> FitResult:
    """Render the positions as a Rich table, fitted to the terminal.

    Rows are grouped by the currency they trade in, each having its own subtotal.
    Since the values are currency-denominated, the grouping is mandatory in order
    for sensible sorting.

    A currency group that has any closed positions gets one more aggregated row at the
    bottom. You can see them all when `show_closed` is True.

    Args:
        holdings: The valued positions to show.
        title: Table title.
        flows: The pool's cash movements, so the grand total can state its
            return against net deposits. Without it that one cell is blank.
        wide: Offer the wide-only columns as well.
        show_closed: Break the aggregate "Closed" row open into one row per
            closed position instead.

    Returns:
        The fitted table alongside the columns it had to give up, so the
        caller can say what is missing.
    """
    specs = _columns(holdings, wide=wide)
    table = RichTable(
        title=title,
        show_header=True,
        header_style="bold bright_white",
        border_style="bright_blue",
    )
    for spec in specs:
        table.add_column(
            spec.header,
            justify=spec.justify,
            no_wrap=spec.header != "Name",
        )

    deposited = _net_deposited(flows)
    for group in holdings.by_currency:
        members = [h for h in holdings.holdings if h.currency is group.currency]
        for holding in members:
            table.add_row(*[spec.render(holding) for spec in specs])

        closed_members = [h for h in holdings.closed if h.currency is group.currency]
        if closed_members:
            if show_closed:
                for holding in closed_members:
                    table.add_row(*_closed_cells(holding, specs))
            else:
                table.add_row(*_closed_summary_cells(closed_members, specs))

        # A rule above each total says "this row is a different kind of thing"
        # in one line rather than a blank one, keeping the table short.
        table.add_section()
        table.add_row(
            *_subtotal_cells(
                group,
                specs,
                _subtotal_return(holdings, group, deposited),
            ),
            style=SUBTOTAL_ROW_STYLE,
        )
        table.add_section()

    # For multiple currencies, show an additional grand-total row.
    if holdings.mixed_currency:
        table.add_row(
            *_grand_total_cells(holdings, specs, flows),
            style=GRAND_TOTAL_ROW_STYLE,
        )

    return fit(table, _DASH_DROP_ORDER)


def _closed_cells(holding: Holding, specs: Sequence[_ColumnSpec]) -> list[str]:
    """Render one closed position, blanking every column that no longer applies."""
    total = holding.closed_total
    cells = {
        "Symbol": f"{holding.symbol} [dim](closed)[/dim]",
        "Realized": _signed(
            _money(holding.realized, blank_zero=True),
            holding.realized,
        ),
        "Divs": _income(holding.dividends),
        "Total": _signed(_money(total, blank_zero=True), total),
    }
    return [cells.get(spec.header, "") for spec in specs]


def _closed_summary_cells(
    closed: Sequence[Holding],
    specs: Sequence[_ColumnSpec],
) -> list[str]:
    """Aggregate every closed position in one currency group into one row."""
    totals = summarize_closed(closed)
    total = totals.total
    cells = {
        "Symbol": f"[bold dim]Closed ({totals.count})[/bold dim]",
        "Realized": _signed(_money(totals.realized, blank_zero=True), totals.realized),
        "Divs": _income(totals.dividends),
        "Total": _signed(_money(total, blank_zero=True), total),
    }
    return [cells.get(spec.header, "") for spec in specs]


@dataclass(frozen=True)
class _Return:
    """What one totals row's `Total%` states, and what colours it.

    Attributes:
        ratio: The percentage to print, or None to leave the cell blank.
        earned: The gain the ratio came from, whose sign picks the colour.
    """

    ratio: Decimal | None = None
    earned: Decimal | None = None


def _subtotal_return(
    holdings: HoldingSet,
    group: CurrencyTotals,
    deposited: Decimal | None,
) -> _Return:
    """Decide whether a currency subtotal is really the pool's overall total.

    A USD-only pool is still one pool with one deposit history, so it earns the
    same promotion a CAD-only one does, and `return_on` keeps both sides of the
    ratio in CAD however the pool is displayed.

    Args:
        holdings: The set being rendered.
        group: The currency group this row totals.
        deposited: Net CAD deposits, when the caller could supply them.

    Returns:
        The promoted return when this row is the pool's overall total, the
        group's own book-based ratio when a grand total is coming anyway, and a
        blank when `-c USD` has hidden part of the pool from a whole-pool
        denominator.
    """
    # no need to promote, we are going to show a grand total anyway
    if holdings.mixed_currency:
        return _Return(group.total_pnl_pct, group.total_pnl)
    if not holdings.deposits_measurable:
        # Holdings are being hidden, we cant calculate truthful `Total%`
        return _Return()
    promoted = holdings.return_on(deposited)
    if promoted is None:
        return _Return(group.total_pnl_pct, group.total_pnl)
    return _Return(promoted, holdings.total_pnl_cad)


def _subtotal_cells(
    group: CurrencyTotals,
    specs: Sequence[_ColumnSpec],
    total: _Return,
) -> list[str]:
    """Subtotal one currency group, in that group's own currency.

    Every figure here is read off `group`, which the engine already totalled.

    Args:
        group: The currency group's totals.
        specs: The columns this table is rendering.
        total: What the `Total%` cell should say, from `_subtotal_return`.

    Returns:
        One cell per column in `specs`.
    """
    cells = {
        "Symbol": f"[bold]{group.count} held[/bold]",
        # A totals row has no unit count, and the column is never conceded, so
        # it is free space in exactly the place the currency belongs.
        "Units": _currency(group.currency, str(group.currency)),
        "Book": _money(group.book),
        "Market": _money(group.market),
        "PnL": _signed(_money(group.day_pnl), group.day_pnl),
        "PnL%": _day_move(group.day_pnl_pct),
        "Unreal": _signed(_money(group.unrealized), group.unrealized),
        "Unreal%": _return(group.unrealized_pct, group.unrealized),
        "Realized": _signed(
            _money(group.realized, blank_zero=True),
            group.realized,
        ),
        "Divs": _income(group.dividends),
        "Total": _signed(_money(group.total_pnl), group.total_pnl),
        "Total%": _return(total.ratio, total.earned),
        "Wt%": _percent(group.weight_in_pool),
        "Folio%": _percent(group.weight_in_folio),
    }
    return [cells.get(spec.header, "") for spec in specs]


def _grand_total_cells(
    holdings: HoldingSet,
    specs: Sequence[_ColumnSpec],
    flows: Flows | None = None,
) -> list[str]:
    """Total across every currency, converted into the base currency."""
    base = holdings.base_currency
    deposited = _net_deposited(flows)
    whole = _percent(Decimal(1)) if holdings.total_market else ""
    cells = {
        "Symbol": "[bold]Total[/bold]",
        "Units": _currency(base, f"({base})"),
        "Wt%": whole,
        # How much of everything owned this whole scope accounts for
        "Folio%": _percent(holdings.total_weight_in_folio),
        "Book": _money(holdings.total_book),
        "Market": _money(holdings.total_market),
        "PnL": _signed(_money(holdings.total_day_pnl), holdings.total_day_pnl),
        "Unreal": _signed(_money(holdings.total_unrealized), holdings.total_unrealized),
        "Realized": _signed(
            _money(holdings.total_realized, blank_zero=True),
            holdings.total_realized,
        ),
        "Divs": _income(holdings.total_dividends),
        "Total": _signed(_money(holdings.total_pnl), holdings.total_pnl),
        "PnL%": _day_move(holdings.total_day_pnl_pct, holdings.total_day_pnl),
        "Unreal%": _return(holdings.total_unrealized_pct, holdings.total_unrealized),
        # Against net deposits rather than book value: see `return_on`.
        "Total%": _return(holdings.return_on(deposited), holdings.total_pnl),
    }
    return [cells.get(spec.header, "") for spec in specs]


def _net_deposited(flows: Flows | None) -> Decimal | None:
    """Read the return denominator, for a caller that may have no flows at all.

    The rule itself (CAD only, judged at cent precision) belongs to the engine:
    see `Flows.net_deposit_denominator`.
    """
    return flows.net_deposit_denominator if flows else None


def flows_panel(flows: Flows, title: str | None = None) -> Panel:
    """Render cash, contributions and cumulative income for one scope.

    Args:
        flows: The pool's figures.
        title: Panel title, defaulting to the pool's label.

    Returns:
        A Rich panel. Cash is listed per currency and never summed across them:
        a blended total would hide an FX assumption and its date.
    """
    # A borderless table rather than a grid, so `add_section` can draw a faint
    # rule between capital, earnings and room. The one-space divider plus one
    # collapsed space of padding keeps the grid's two-space gap, and its width.
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
        title=_panel_title(flows, title),
        border_style="red" if flows.negative_currencies else "bright_blue",
        expand=False,
    )


def _panel_title(flows: Flows, title: str | None) -> str:
    """Name the panel, accented by the pool's account type when it has one."""
    name = title or flows.label
    if flows.account_type is None:
        return name
    accent = ACCOUNT_TYPE_COLORS.get(flows.account_type)
    return _style(name, f"bold {accent}" if accent else None)


# The first measure past capital, where the earnings section begins.
_EARNINGS_START = "Dividends"


def _add_money_rows(table: RichTable, flows: Flows) -> None:
    """Add one labelled row per measure, one line per currency it holds."""
    measures = (
        ("Cash", flows.cash, True),
        ("Contributions", flows.contributions, False),
        ("Withdrawn", flows.withdrawals, False),
        ("Transferred", flows.transfers, False),
        ("Net Deposited", flows.net_deposited, False),
        ("Dividends", flows.dividends, False),
        ("Fees", flows.fees, False),
        ("Realized", flows.realized, False),
    )
    for label, amounts, alert in measures:
        # A no-op until a row exists, so an empty capital group draws no rule.
        if label == _EARNINGS_START:
            table.add_section()
        if not amounts:
            continue
        moved = flows.transfers_value if amounts is flows.transfers else {}
        for index, currency in enumerate(sorted(amounts, key=str)):
            amount = amounts[currency]
            note = _currency(currency, str(currency))
            actual = moved.get(currency)
            # When transfers value and transfer are not the same.
            if actual is not None and q2(actual) != q2(amount):
                note += _style(f"  of {_money(actual)} moved", "dim")
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
    text = _money(amount)
    if alert:
        return _style(text, LOSS) if q2(amount) < ZERO else text
    if label == "Realized":
        return _signed(text, q2(amount))
    return _style(text, FLOW_COLORS.get(label))


def _room_rows(room: Room) -> list[tuple[str, str, str]]:
    """Render the contribution-room lines for a registered pool.

    With a limit, a gauge follows on its own line, exactly as wide as the
    figures above it, so the panel grows a row rather than a column.
    """
    label = f"Room {room.year}"
    used = _money(room.used)
    remaining = room.remaining
    if room.limit is None or remaining is None:
        return [(label, used, _style("contributed, no limit configured", "dim"))]
    figures = f"{used} / {_money(room.limit)}"
    colour = _room_colour(room)
    if room.full:
        note = "full"
    elif room.over:
        note = f"{_money(abs(remaining))} over"
    else:
        note = f"{_money(remaining)} left"
    rows = [(label, _style(figures, colour), _style(note, "dim"))]
    if room.used_ratio is not None:
        glyphs = ROOM_BAR if _UNICODE else ROOM_BAR_ASCII
        gauge = meter(room.used_ratio, len(figures), glyphs)
        rows.append(("", _style(gauge, colour), ""))
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

    This is the highest-value alert the app has: it needs no brokerage balance
    to detect, and it catches a missing transaction immediately.
    """
    negative = flows.negative_currencies
    if not negative:
        return []
    currencies = ", ".join(str(currency) for currency in negative)
    return [
        "",
        (
            f"[red]{_WARN_GLYPH} Cash is negative ({currencies}). A transaction is "
            f"probably missing.[/red]\n[dim]  Run `folio check` to find it.[/dim]"
        ),
    ]


def show_by_type(
    panels: Sequence[tuple[HoldingSet, Flows, str]],
    *,
    wide: bool = False,
    show_closed: bool = False,
    badge: str | None = None,
) -> None:
    """Print one dashboard per account type, flows tiled above the holdings.

    The flows panels are narrow and similar in height, so they tile into a
    strip across the top. The holdings tables are each nearly as wide as the
    terminal, so they could never sit side by side and follow in full width,
    each under a heading of its own.

    Args:
        panels: Each type's valued positions, flows and label.
        wide: Offer the wide-only columns.
        show_closed: Break the aggregate "Closed" row open per position.
        badge: Cache-freshness line, printed above everything.
    """
    if badge:
        console_print(badge)

    strip = [
        Block.create(
            name=title,
            key=title[:1].lower(),
            panel=flows_panel(flows, title),
            total=0,
            shown=0,
            data_type="flows",
            data=(holdings, flows, title),
        )
        for holdings, flows, title in panels
    ]
    layout = TilingLayout(strip)
    layout.render()

    # The tiling reorders the panels, so the sections follow its reading order
    # rather than the given one. Split from its flows panel, each table needs a
    # heading to say whose it is.
    for block in layout.all_blocks:
        holdings, flows, title = block.data
        accent = ""
        if flows.account_type is not None:
            accent = ACCOUNT_TYPE_COLORS.get(flows.account_type, "")
        console_print("")
        console_print(Rule(_panel_title(flows, title), align="left", style=accent))
        _print_holdings(holdings, flows, wide=wide, show_closed=show_closed)


def show_dashboard(
    holdings: HoldingSet,
    flows: Flows,
    title: str,
    *,
    wide: bool = False,
    show_closed: bool = False,
    badge: str | None = None,
) -> None:
    """Print one scope's dashboard: badge, flows panel, then holdings.

    Args:
        holdings: The valued positions.
        flows: The pool's cash and income figures.
        title: Heading for both the panel and the table.
        wide: Offer the wide-only columns.
        show_closed: Break the aggregate "Closed" row open per position.
        badge: Cache-freshness line, printed above everything.
    """
    if badge:
        console_print(badge)
    console_print(flows_panel(flows, title))
    _print_holdings(holdings, flows, wide=wide, show_closed=show_closed)


def _print_holdings(
    holdings: HoldingSet,
    flows: Flows,
    *,
    wide: bool,
    show_closed: bool,
) -> None:
    """Print one pool's holdings table with everything it has to disclose.

    Args:
        holdings: The valued positions.
        flows: The pool's cash and income figures.
        wide: Offer the wide-only columns.
        show_closed: Break the aggregate "Closed" row open per position.
    """
    if not holdings.holdings and not holdings.closed:
        console_print("[yellow]No open positions in this pool.[/yellow]")
        return

    fitted = holdings_table(holdings, flows=flows, wide=wide, show_closed=show_closed)
    if fitted.note:
        console_print(fitted.note)
    console_print(fitted.table)
    for note in _footnotes(holdings, flows):
        console_print(note)


def _footnotes(holdings: HoldingSet, flows: Flows | None = None) -> list[str]:
    """Disclose what the table could not show and what it converted at."""
    notes: list[str] = []
    if holdings.unpriced:
        listed = ", ".join(holdings.unpriced)
        notes.append(
            f"[yellow]{_WARN_GLYPH} {len(holdings.unpriced)} position(s) unpriced "
            f"and excluded from totals: {listed}[/yellow]",
        )
    if not holdings.deposits_measurable and _net_deposited(flows) is not None:
        notes.append(
            f"[dim]Total% is blank: it measures the pool against its net "
            f"deposits, and {holdings.excluded} CAD position(s) are hidden by "
            f"--currency {holdings.base_currency}. Drop the flag to see "
            f"it.[/dim]",
        )
    # A single-currency pool that isn't CAD still gets its `Total%` promoted to
    # net deposits (see `_subtotal_return`), which is a CAD figure, so that one cell
    # is a conversion even though every other column stays untouched. Only worth
    # disclosing when the promotion actually filled the cell: with no CAD deposits to
    # divide by, `Total%` is blank and there is nothing to have converted.
    single_promoted = (
        not holdings.mixed_currency
        and holdings.deposits_measurable
        and holdings.by_currency
        and holdings.by_currency[0].currency is not Currency.CAD
        and _net_deposited(flows) is not None
    )
    # `-c USD` converts nothing: the CAD holdings are eliminated.
    converts = (
        holdings.mixed_currency
        or holdings.display_currency == Currency.CAD
        or single_promoted
    )
    if holdings.fx_rate is not None and converts:
        if holdings.mixed_currency:
            what = f"Total ({holdings.base_currency}) converted"
        elif single_promoted:
            what = "Total% (CAD) converted"
        else:
            what = "Valued"
        notes.append(
            f"[dim]{what} at USDCAD {float(holdings.fx_rate):,.4f} "
            f"({holdings.fx_date}). Cost base keeps its own historical "
            f"rates.[/dim]",
        )
    suspect = [code for code in holdings.flags if code in _UNITS_SUSPECT]
    if suspect:
        codes = ", ".join(str(code) for code in suspect)
        notes.append(
            f"[red]{_WARN_GLYPH} Badged positions carry {codes}; their units are "
            f"known to be wrong. Run `folio check`.[/red]",
        )
    return notes


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
