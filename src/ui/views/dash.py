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


@dataclass(frozen=True)
class _ColumnSpec:
    """How one dashboard column is rendered.

    Attributes:
        header: Column heading.
        cell: Reads the rendered string for one holding.
        justify: Alignment.
        wide_only: Shown only when `--wide` asked for it.
    """

    header: str
    cell: Callable[[Holding], str]
    justify: JustifyMethod = "right"
    wide_only: bool = False


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


def _signed(text: str, value: Decimal | None) -> str:
    """Colour a rendered cell by the sign of the number behind it.

    Green up, red down, matching every other table in the app.
    """
    if not text or value is None or value == 0 or text == _EM_DASH:
        return text
    colour = "green" if value > 0 else "red"
    return f"[{colour}]{text}[/{colour}]"


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
    _ColumnSpec("Units", lambda h: _units(h.units)),
    _ColumnSpec("Avg", lambda h: _price(h.avg_cost)),
    _ColumnSpec("Last", lambda h: _price(h.price)),
    _ColumnSpec("Change", lambda h: _signed(_price(h.change), h.change)),
    _ColumnSpec("Change%", lambda h: _signed(_percent(h.change_pct), h.change_pct)),
    _ColumnSpec("PnL", lambda h: _signed(_money(h.day_pnl), h.day_pnl)),
    _ColumnSpec("PnL%", lambda h: _signed(_percent(h.day_pnl_pct), h.day_pnl_pct)),
    _ColumnSpec("Unreal", lambda h: _signed(_money(h.unrealized), h.unrealized)),
    _ColumnSpec(
        "Unreal%",
        lambda h: _signed(_percent(h.unrealized_pct), h.unrealized_pct),
    ),
    # Realized and Divs blank their zeros
    _ColumnSpec(
        "Realized",
        lambda h: _signed(_money(h.realized, blank_zero=True), h.realized),
    ),
    _ColumnSpec("Divs", lambda h: _money(h.dividends, blank_zero=True)),
    _ColumnSpec("Total", lambda h: _signed(_money(h.total_pnl), h.total_pnl)),
    _ColumnSpec(
        "Total%",
        lambda h: _signed(_percent(h.total_pnl_pct), h.total_pnl_pct),
    ),
    _ColumnSpec("Book", lambda h: _money(h.book_value)),
    _ColumnSpec("Market", lambda h: _money(h.market_value)),
    _ColumnSpec("Wt%", lambda h: _percent(h.weight_in_pool)),
    _ColumnSpec("Folio%", lambda h: _percent(h.weight_in_folio)),
)

# Column drop order as terminals get too narrow.
_DASH_DROP_ORDER = (
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
    narrowed = holdings.scope.name != "FOLIO"
    return tuple(
        spec
        for spec in _HOLDING_COLUMNS
        # `Folio%` is the same number as `Wt%` on the portfolio-wide view, so it
        # is dropped outright there rather than shown twice.
        if (wide or not spec.wide_only) and (narrowed or spec.header != "Folio%")
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
            table.add_row(*[spec.cell(holding) for spec in specs])

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
        promoted = _promoted_return(holdings, deposited)
        table.add_row(
            *_subtotal_cells(group, specs, promoted, holdings.total_pnl),
        )
        table.add_section()

    # For multiple currencies, show an additional grand-total row.
    if holdings.mixed_currency:
        table.add_row(*_grand_total_cells(holdings, specs, flows))

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
        "Divs": _money(holding.dividends, blank_zero=True),
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
        "Divs": _money(totals.dividends, blank_zero=True),
        "Total": _signed(_money(total, blank_zero=True), total),
    }
    return [cells.get(spec.header, "") for spec in specs]


def _promoted_return(
    holdings: HoldingSet,
    deposited: Decimal | None,
) -> Decimal | None:
    """Decide whether a currency subtotal is really the pool's overall total.

    A USD-only pool is still one pool with one deposit history, so it earns the
    same promotion a CAD-only one does. `return_on` divides the pool's CAD
    earnings by its CAD deposits, so the ratio calc is in same currency (regardless
    of the pools original currency)

    Args:
        holdings: The set being rendered.
        deposited: Net CAD deposits, when the caller could supply them.

    Returns:
        The return to show in this row's `Total%`, or None to keep the group's
        own book-based ratio.
    """
    # no need to promote, we are going to show a grand total anyway
    if holdings.mixed_currency:
        return None
    return holdings.return_on(deposited)


def _subtotal_cells(
    group: CurrencyTotals,
    specs: Sequence[_ColumnSpec],
    promoted_return: Decimal | None = None,
    promoted_total_pnl: Decimal | None = None,
) -> list[str]:
    """Subtotal one currency group, in that group's own currency.

    Every figure here is read off `group`, which the engine already totalled.

    Args:
        group: The currency group's totals.
        specs: The columns this table is rendering.
        promoted_return: The pool's return on net deposits, when `_promoted_return`
            has judged this row to be the pool's overall total. None keeps the
            group's own book-based ratio.
        promoted_total_pnl: The pool's total earnings in CAD, which decides the
            promoted cell's sign. Ignored unless `promoted_return` is not None;
            a USD-only group's own `total_pnl` is in USD and would be the wrong
            side of that ratio.

    Returns:
        One cell per column in `specs`.
    """
    move = group.day_pnl_pct
    # Promotion swaps both sides of the ratio at once: net deposits is a CAD
    # figure, so it has to be paired with the pool's CAD earnings rather than
    # the group's native ones.
    if promoted_return is None:
        total_pct_numerator = group.total_pnl
        total_pct = group.total_pnl_pct
    else:
        total_pct_numerator = promoted_total_pnl
        total_pct = promoted_return
    cells = {
        "Symbol": f"[bold]{group.count} held[/bold]",
        # A totals row has no unit count, and the column is never conceded, so
        # it is free space in exactly the place the currency belongs.
        "Units": f"[dim]{group.currency}[/dim]",
        "Book": _money(group.book),
        "Market": _money(group.market),
        "PnL": _signed(_money(group.day_pnl), group.day_pnl),
        "PnL%": _signed(_percent(move), move),
        "Unreal": _signed(_money(group.unrealized), group.unrealized),
        "Unreal%": _signed(_percent(group.unrealized_pct), group.unrealized),
        "Realized": _signed(
            _money(group.realized, blank_zero=True),
            group.realized,
        ),
        "Divs": _money(group.dividends, blank_zero=True),
        "Total": _signed(_money(group.total_pnl), group.total_pnl),
        "Total%": _signed(_percent(total_pct), total_pct_numerator),
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
        "Units": f"[dim]({base})[/dim]",
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
        "Divs": _money(holdings.total_dividends, blank_zero=True),
        "Total": _signed(_money(holdings.total_pnl), holdings.total_pnl),
        "PnL%": _signed(
            _percent(holdings.total_day_pnl_pct),
            holdings.total_day_pnl,
        ),
        "Unreal%": _signed(
            _percent(holdings.total_unrealized_pct),
            holdings.total_unrealized,
        ),
        # Against net deposits rather than book value: see `return_on`.
        "Total%": _signed(
            _percent(holdings.return_on(deposited)),
            holdings.total_pnl,
        ),
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
    table = RichTable.grid(padding=(0, 2))
    table.add_column(justify="left", style="bold")
    table.add_column(justify="right")
    table.add_column(justify="left", style="dim")

    _add_money_rows(table, flows)

    if flows.room is not None:
        table.add_row(*_room_row(flows))

    body: list[RenderableType] = [table]
    body.extend(_cash_alerts(flows))

    return Panel(
        Group(*body),
        title=title or flows.label,
        border_style="red" if flows.negative_currencies else "bright_blue",
        expand=False,
    )


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
        if not amounts:
            continue
        moved = flows.transfers_value if amounts is flows.transfers else {}
        for index, currency in enumerate(sorted(amounts, key=str)):
            amount = amounts[currency]
            text = _money(amount)
            if alert and q2(amount) < ZERO:
                text = f"[red]{text}[/red]"
            note = str(currency)
            actual = moved.get(currency)
            # When transfers value and transfer are not the same.
            if actual is not None and q2(actual) != q2(amount):
                note = f"{note}  of {_money(actual)} moved"
            table.add_row(label if index == 0 else "", text, note)


def _room_row(flows: Flows) -> tuple[str, str, str]:
    """Render the contribution-room line for a registered pool."""
    room = flows.room
    if room is None:  # pragma: no cover - guarded by the caller
        return ("", "", "")
    used = _money(room.used)
    if room.limit is None:
        return (f"Room {room.year}", used, "contributed, no limit configured")
    limit = _money(room.limit)
    colour = "red" if room.over else "green"
    return (
        f"Room {room.year}",
        f"[{colour}]{used} / {limit}[/{colour}]",
        f"{_money(room.remaining)} left",
    )


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


def holdings_block(
    holdings: HoldingSet,
    flows: Flows,
    title: str,
    *,
    wide: bool = False,
    show_closed: bool = False,
) -> Block:
    """Group one scope's panel and table into a tileable block.

    Args:
        holdings: The valued positions.
        flows: The pool's cash and income figures.
        title: Block name, also the panel title.
        wide: Offer the wide-only columns.
        show_closed: Break the aggregate "Closed" row open per position.

    Returns:
        A measured `Block` for `TilingLayout`.
    """
    fitted = holdings_table(holdings, flows=flows, wide=wide, show_closed=show_closed)
    body: list[RenderableType] = [flows_panel(flows, title)]
    if fitted.note:
        body.append(fitted.note)
    body.append(fitted.table)
    panel = Group(*body)
    return Block.create(
        name=title,
        key=title[:1].lower(),
        panel=panel,
        total=len(holdings.holdings),
        shown=len(holdings.holdings),
        data_type="holdings",
        data=holdings.holdings,
    )


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
    # A single-currency pool that isn't CAD still gets its `Total%` promoted to
    # net deposits (see `_promoted_return`), which is a CAD figure, so that one cell
    # is a conversion even though every other column stays untouched. Only worth
    # disclosing when the promotion actually filled the cell: with no CAD deposits to
    # divide by, `Total%` is blank and there is nothing to have converted.
    single_promoted = (
        not holdings.mixed_currency
        and holdings.display_currency == "native"
        and holdings.by_currency
        and holdings.by_currency[0].currency is not Currency.CAD
        and _net_deposited(flows) is not None
    )
    converts = (
        holdings.mixed_currency
        or holdings.display_currency != "native"
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
