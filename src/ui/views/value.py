"""Rendering market values on past dates: one date, month-ends, one holding."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich import box
from rich.table import Table as RichTable

from domain import AccountType, Currency, Fmt
from engine.accounts import account_type_of
from engine.past_values import month_end_pools, unpriced_month_ends
from term import console_print
from ui.cells import WARN_GLYPH, money, price, style, units
from ui.layout.columns import FormattedColumn, add_column
from ui.layout.fit import fit_table
from ui.vocabulary import (
    ACCOUNT_TYPE_COLORS,
    GRAND_TOTAL_ROW_STYLE,
    SHORT_ACCOUNT_TYPES,
    TABLE_BORDER,
    TABLE_HEADER,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from decimal import Decimal

    from engine.accounts import Pool
    from engine.holdings import DisplayCurrency
    from engine.past_values import DatedPool, DatedValuation, PoolRows

# Marks a value that leaves out an unpriced holding.
_UNPRICED_MARK = "*"
# Stands in for a pool that held nothing yet, or a holding with no price.
_NOTHING = "-"
# Currency lines in the order a native table lists them.
_LINES = (Currency.CAD, Currency.USD)
_IN_TRANSIT = "In transit"
_TOTAL = "Total"


def show_value(
    valued: DatedValuation,
    rows: PoolRows,
    into: DisplayCurrency,
    *,
    capped: bool,
    by_type: bool,
) -> None:
    """Print each listed pool's holdings, cash and value, then what they leave out.

    Args:
        valued: Every pool on the valuation date.
        rows: The pools listed and the pool they total.
        into: The currency every figure is converted into, or native to keep
            each currency line apart.
        capped: The day asked had not closed, so the latest close stands in.
        by_type: The rows are account types, not accounts.
    """
    title = _title(valued, str(into), capped=capped)
    if into == "native":
        table = _native_table(rows, by_type=by_type)
    else:
        table = _converted_table(rows, into, by_type=by_type)
    _print_table(title, table)
    if into != "native" and rows.total.converts_into(into):
        console_print(_conversion_line(valued, into))
    for line in _twin_lines(rows.total.twins):
        console_print(line)
    for held in valued.unpriced(rows.total.pool):
        console_print(_unpriced_line(held.symbol, held.account, units(held.units)))


def show_months(
    valuations: Sequence[DatedValuation],
    pool: Pool,
    into: Currency,
    *,
    by_type: bool,
    capped: bool,
) -> None:
    """Print each month-end as a row, each listed pool's value as a column.

    Args:
        valuations: Every pool at each month-end, in order.
        pool: The pool to report, whose value is the Total column.
        into: The currency every figure is converted into.
        by_type: The columns are account types, not accounts.
        capped: The last month had not closed, so the latest close stands in.
    """
    final = valuations[-1]
    if pool not in final.pools:
        console_print(f"{pool.label} held nothing by {final.on}.")
        return
    listed = month_end_pools(valuations, pool, by_type=by_type)
    table = _table()
    add_column(table, FormattedColumn("Month", no_wrap=True))
    for member in listed:
        header = _labels(member, by_type=by_type)[0]
        add_column(table, FormattedColumn(header, fmt=Fmt.MONEY, justify="right"))
    add_column(table, FormattedColumn(_TOTAL, fmt=Fmt.MONEY, justify="right"))
    for valued in valuations:
        cells = [
            _value_cell(valued.pools.get(member), into) for member in (*listed, pool)
        ]
        table.add_row(f"{valued.asked:%Y-%m}", *cells)
    _print_table(f"Market value at each month-end · {into}", table)

    if capped:
        console_print(
            f"[dim]{final.asked:%Y-%m} is valued at the latest close, "
            f"{final.on}.[/dim]",
        )
    totals = [valued.pools[pool] for valued in valuations if pool in valued.pools]
    if any(dated.converts_into(into) for dated in totals):
        console_print(
            f"[dim]Converted to {into} at each month-end's Bank of Canada rate.[/dim]",
        )
    twins = {symbol: twin for dated in totals for symbol, twin in dated.twins.items()}
    for line in _twin_lines(twins):
        console_print(line)
    for (symbol, account), months in unpriced_month_ends(valuations, pool).items():
        at = ", ".join(f"{month:%Y-%m}" for month in months)
        console_print(_unpriced_line(symbol, account, f"at {at}"))


def show_holding(
    valued: DatedValuation,
    rows: PoolRows,
    symbol: str,
    into: DisplayCurrency,
    *,
    capped: bool,
    by_type: bool,
) -> None:
    """Print one security's units, price and value in each listed pool, and its total.

    Args:
        valued: Every pool on the valuation date.
        rows: The pools listed and the pool they total, which holds the security.
        symbol: The security.
        into: The currency values are converted into, or native to leave them
            in the security's own.
        capped: The day asked had not closed, so the latest close stands in.
        by_type: The rows are account types, not accounts.
    """
    total = rows.total.held(symbol)
    if total is None:
        return
    shown = total.currency if into == "native" else into
    table = _table()
    _add_label_columns(table, by_type=by_type)
    add_column(table, FormattedColumn("Units", fmt=Fmt.UNITS, justify="right"))
    add_column(
        table,
        FormattedColumn(f"Price {total.currency}", fmt=Fmt.PRICE, justify="right"),
    )
    add_column(table, FormattedColumn("Value", fmt=Fmt.MONEY, justify="right"))
    blanks = _blanks(by_type=by_type)
    for dated, held in rows.holdings(symbol):
        table.add_row(
            *_labels(dated.pool, by_type=by_type),
            units(held.units),
            price(held.price),
            _money_or_nothing(dated.market_value_of(held, shown)),
        )
    if (moving := rows.holding_in_transit(symbol)) is not None:
        table.add_row(
            _IN_TRANSIT,
            *blanks,
            units(moving.units),
            price(moving.price),
            _money_or_nothing(rows.total.market_value_of(moving, shown)),
        )
    table.add_section()
    table.add_row(
        _TOTAL,
        *blanks,
        units(total.units),
        price(total.price),
        _money_or_nothing(rows.total.market_value_of(total, shown)),
        style=GRAND_TOTAL_ROW_STYLE,
    )
    title = _title(valued, str(shown), capped=capped, subject=f"of {symbol} ")
    _print_table(title, table)
    if shown != total.currency:
        console_print(_conversion_line(valued, shown))
    for line in _twin_lines({symbol: total.twin} if total.twin else {}):
        console_print(line)
    for held in valued.unpriced(rows.total.pool):
        if held.symbol == symbol:
            console_print(_unpriced_line(held.symbol, held.account, units(held.units)))


def _converted_table(rows: PoolRows, into: Currency, *, by_type: bool) -> RichTable:
    """Render each listed pool's holdings, cash and value in one currency."""
    table = _table()
    _add_label_columns(table, by_type=by_type)
    _add_money_columns(table)
    blanks = _blanks(by_type=by_type)
    for dated in rows.rows:
        table.add_row(*_labels(dated.pool, by_type=by_type), *_figures(dated, into))
    if transit := rows.in_transit(into):
        table.add_row(_IN_TRANSIT, *blanks, "", "", money(transit))
    table.add_section()
    table.add_row(
        _TOTAL,
        *blanks,
        *_figures(rows.total, into),
        style=GRAND_TOTAL_ROW_STYLE,
    )
    return table


def _native_table(rows: PoolRows, *, by_type: bool) -> RichTable:
    """Render each listed pool's currency lines, each left in its own currency."""
    table = _table()
    _add_label_columns(table, by_type=by_type)
    add_column(table, FormattedColumn("$", fmt=Fmt.CURRENCY, no_wrap=True))
    _add_money_columns(table)
    blanks = _blanks(by_type=by_type)
    for dated in rows.rows:
        labels = _labels(dated.pool, by_type=by_type)
        for currency in _held_lines(dated):
            table.add_row(*labels, str(currency), *_line_figures(dated, currency))
            labels = ["", *blanks]
    for currency in _LINES:
        if transit := rows.line_in_transit(currency):
            table.add_row(_IN_TRANSIT, *blanks, str(currency), "", "", money(transit))
    table.add_section()
    label = _TOTAL
    for currency in _held_lines(rows.total):
        table.add_row(
            label,
            *blanks,
            str(currency),
            *_line_figures(rows.total, currency),
            style=GRAND_TOTAL_ROW_STYLE,
        )
        label = ""
    return table


def _table() -> RichTable:
    """Start a borderless table: a rule under the header and before the total."""
    return RichTable(
        box=box.SIMPLE,
        show_edge=False,
        header_style=TABLE_HEADER,
        border_style=TABLE_BORDER,
    )


def _print_table(title: str, table: RichTable) -> None:
    """Print the title on its own line, unwrapped, then the table."""
    console_print(f"[italic]{title}[/italic]")
    console_print(fit_table(table))


def _add_label_columns(table: RichTable, *, by_type: bool) -> None:
    """Add the columns naming each row: an account and its type, or a type."""
    if not by_type:
        add_column(table, FormattedColumn("Account", no_wrap=True))
    add_column(table, FormattedColumn("Type", no_wrap=True))


def _add_money_columns(table: RichTable) -> None:
    """Add the holdings, cash and value columns."""
    for header in ("Holdings", "Cash", "Value"):
        add_column(table, FormattedColumn(header, fmt=Fmt.MONEY, justify="right"))


def _blanks(*, by_type: bool) -> list[str]:
    """Fill the label columns a total or in-transit row leaves after its name."""
    return [] if by_type else [""]


def _title(
    valued: DatedValuation,
    shown: str,
    *,
    capped: bool,
    subject: str = "",
) -> str:
    """Name the valuation date, and why it differs from the day asked."""
    note = ""
    if capped:
        note = " (latest close)"
    elif valued.on != valued.asked:
        note = f" (close before {valued.asked})"
    return f"Market value {subject}at {valued.on}{note} · {shown}"


def _labels(pool: Pool, *, by_type: bool) -> list[str]:
    """Name a row: an account and its type, or a type alone, in the type's colour."""
    account_type = account_type_of(pool) or AccountType.UNKNOWN
    tint = ACCOUNT_TYPE_COLORS.get(account_type)
    short = SHORT_ACCOUNT_TYPES.get(account_type, str(account_type))
    if by_type:
        return [style(short, tint)]
    return [style(pool.name, tint), style(short, tint)]


def _held_lines(dated: DatedPool) -> list[Currency]:
    """List the currency lines a pool holds anything in, CAD first."""
    held = [currency for currency in _LINES if currency in dated.currencies]
    return held or [Currency.CAD]


def _figures(dated: DatedPool, into: Currency) -> tuple[str, str, str]:
    """Render a pool's holdings, cash and value, marking a value with gaps."""
    return (
        money(dated.holdings_market_value(into)),
        money(dated.cash_in(into)),
        _value_cell(dated, into),
    )


def _line_figures(dated: DatedPool, currency: Currency) -> tuple[str, str, str]:
    """Render one currency line's holdings, cash and value, left in that currency."""
    mark = _UNPRICED_MARK if dated.line_unpriced(currency) else ""
    return (
        money(dated.line_holdings(currency)),
        money(dated.line_cash(currency)),
        f"{money(dated.line_market_value(currency))}{mark}",
    )


def _value_cell(dated: DatedPool | None, into: Currency) -> str:
    """Render a pool's value, marking any gaps, or a dash before it held anything."""
    if dated is None:
        return _NOTHING
    mark = _UNPRICED_MARK if dated.unpriced else ""
    return f"{money(dated.market_value(into))}{mark}"


def _money_or_nothing(value: Decimal | None) -> str:
    """Render a value, or a dash for an unpriced one."""
    return _NOTHING if value is None else money(value)


def _conversion_line(valued: DatedValuation, into: Currency) -> str:
    """Say which rate a single date's figures were converted at."""
    rate = next(iter(valued.pools.values())).usdcad
    return (
        f"[dim]Converted to {into} at the {valued.rate_date} Bank of Canada rate, "
        f"USDCAD {float(rate):,.4f}.[/dim]"
    )


def _twin_lines(twins: dict[str, str]) -> list[str]:
    """Say which US dollar lines were priced from their Canadian dollar twin."""
    return [
        f"[dim]{symbol} priced from {twin} at each day's USDCAD rate.[/dim]"
        for symbol, twin in sorted(twins.items())
    ]


def _unpriced_line(symbol: str, account: str | None, detail: str) -> str:
    """Name one unpriced holding, where it was and how much or when."""
    where = account or _IN_TRANSIT.lower()
    return (
        f"[yellow]{WARN_GLYPH}{_UNPRICED_MARK} Unpriced, left out of every value: "
        f"{symbol} in {where} ({detail})[/yellow]"
    )
