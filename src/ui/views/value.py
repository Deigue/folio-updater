"""Rendering market values on past valuation dates: one date, a range, one holding."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich import box
from rich.table import Table as RichTable

from domain import AccountType, Currency
from engine.accounts import account_type_of
from engine.past_values import ranged_pools
from exporters.sheet_spec import Fmt
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
    table = _table()
    _add_label_columns(table, by_type=by_type)
    if into == "native":
        add_column(table, FormattedColumn("$", fmt=Fmt.CURRENCY, no_wrap=True))
    for header in ("Holdings", "Cash", "Value"):
        add_column(table, FormattedColumn(header, fmt=Fmt.MONEY, justify="right"))

    blanks = [""] * (0 if by_type else 1)
    for dated in rows.rows:
        labels = _labels(dated, by_type=by_type)
        if into == "native":
            for currency in _held_lines(dated):
                table.add_row(*labels, str(currency), *_line_figures(dated, currency))
                labels = ["", *blanks]
        else:
            table.add_row(*labels, *_figures(dated, into))
    if into != "native" and (transit := rows.in_transit(into)):
        table.add_row("In transit", *blanks, "", "", money(transit))

    table.add_section()
    if into == "native":
        label = "Total"
        for currency in _held_lines(rows.total):
            figures = _line_figures(rows.total, currency)
            table.add_row(
                label,
                *blanks,
                str(currency),
                *figures,
                style=GRAND_TOTAL_ROW_STYLE,
            )
            label = ""
    else:
        table.add_row(
            "Total",
            *blanks,
            *_figures(rows.total, into),
            style=GRAND_TOTAL_ROW_STYLE,
        )
    _print_table(title, table)
    for line in _disclosures([valued], rows.total.pool, into):
        console_print(line)


def show_range(
    valuations: Sequence[DatedValuation],
    scope: Pool,
    into: Currency,
    *,
    by_type: bool,
    capped: bool,
) -> None:
    """Print each month-end as a row, each listed pool's value as a column.

    Args:
        valuations: Every pool at each month-end, in order.
        scope: The pool to report, whose value is the Total column.
        into: The currency every figure is converted into.
        by_type: The columns are account types, not accounts.
        capped: The last month had not closed, so the latest close stands in.
    """
    final = valuations[-1]
    if scope not in final.pools:
        console_print(f"Nothing was held by {final.on}.")
        return
    listed = ranged_pools(valuations, scope, by_type=by_type)
    columns = [*listed, scope]
    title = f"Market value at each month-end · {into}"
    table = _table()
    add_column(table, FormattedColumn("Month", no_wrap=True))
    for pool in listed:
        header = _labels(final.pools[pool], by_type=by_type)[0]
        add_column(table, FormattedColumn(header, fmt=Fmt.MONEY, justify="right"))
    add_column(table, FormattedColumn("Total", fmt=Fmt.MONEY, justify="right"))

    for valued in valuations:
        cells = [_value_cell(valued.pools.get(pool), into) for pool in columns]
        table.add_row(f"{valued.asked:%Y-%m}", *cells)
    _print_table(title, table)
    if capped:
        console_print(
            f"[dim]{final.asked:%Y-%m} is valued at the latest close, "
            f"{final.on}.[/dim]",
        )
    for line in _disclosures(valuations, scope, into):
        console_print(line)


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
    title = _title(valued, str(shown), capped=capped, subject=f"of {symbol} ")
    table = _table()
    _add_label_columns(table, by_type=by_type)
    add_column(table, FormattedColumn("Units", fmt=Fmt.UNITS, justify="right"))
    add_column(
        table,
        FormattedColumn(f"Price {total.currency}", fmt=Fmt.PRICE, justify="right"),
    )
    add_column(table, FormattedColumn("Value", fmt=Fmt.MONEY, justify="right"))

    for dated, held in rows.holdings(symbol):
        table.add_row(
            *_labels(dated, by_type=by_type),
            units(held.units),
            price(held.price),
            _money_or_nothing(dated.holding_value(held, shown)),
        )
    table.add_section()
    table.add_row(
        "Total",
        *([""] * (0 if by_type else 1)),
        units(total.units),
        price(total.price),
        _money_or_nothing(rows.total.holding_value(total, shown)),
        style=GRAND_TOTAL_ROW_STYLE,
    )
    _print_table(title, table)
    for line in _disclosures([valued], rows.total.pool, into, symbol=symbol):
        console_print(line)


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
    if by_type:
        add_column(table, FormattedColumn("Type", no_wrap=True))
        return
    add_column(table, FormattedColumn("Account", no_wrap=True))
    add_column(table, FormattedColumn("Type", no_wrap=True))


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


def _labels(dated: DatedPool, *, by_type: bool) -> list[str]:
    """Name a row: an account and its type, or a type alone, in the type's colour."""
    kind = account_type_of(dated.pool) or AccountType.UNKNOWN
    tint = ACCOUNT_TYPE_COLORS.get(kind)
    short = SHORT_ACCOUNT_TYPES.get(kind, str(kind))
    if by_type:
        return [style(short, tint)]
    return [style(dated.pool.name, tint), style(short, tint)]


def _held_lines(dated: DatedPool) -> list[Currency]:
    """List the currency lines a pool holds anything in, CAD first."""
    held = [currency for currency in _LINES if currency in dated.currencies]
    return held or [Currency.CAD]


def _figures(dated: DatedPool, into: Currency) -> tuple[str, str, str]:
    """Render a pool's holdings, cash and value, marking a value with gaps."""
    return (
        money(dated.holdings_value(into)),
        money(dated.cash_value(into)),
        _value_cell(dated, into),
    )


def _line_figures(dated: DatedPool, currency: Currency) -> tuple[str, str, str]:
    """Render one currency line's holdings, cash and value, left in that currency."""
    gaps = any(held.currency is currency for held in dated.unpriced)
    return (
        money(dated.line_holdings(currency)),
        money(dated.line_cash(currency)),
        f"{money(dated.line_value(currency))}{_UNPRICED_MARK if gaps else ''}",
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


def _disclosures(
    valuations: Sequence[DatedValuation],
    scope: Pool,
    into: DisplayCurrency,
    *,
    symbol: str | None = None,
) -> list[str]:
    """Word what the figures were converted at and what they leave out."""
    accounts = [
        dated
        for valued in valuations
        if (rows := valued.rows(scope)) is not None
        for dated in rows.rows
    ]
    lines: list[str] = []
    currencies = {
        currency
        for dated in accounts
        for currency in (
            dated.currencies
            if symbol is None
            else {held.currency for held in dated.holdings if held.symbol == symbol}
        )
    }
    if into != "native" and currencies - {into}:
        if len(valuations) == 1:
            rate = accounts[0].usdcad
            lines.append(
                f"[dim]Converted to {into} at the {valuations[0].rate_date} Bank of "
                f"Canada rate, USDCAD {float(rate):,.4f}.[/dim]",
            )
        else:
            lines.append(
                f"[dim]Converted to {into} at each month-end's Bank of Canada "
                "rate.[/dim]",
            )
    twins: dict[str, str] = {}
    unpriced: set[tuple[str, str]] = set()
    for dated in accounts:
        for held in dated.holdings:
            if symbol is not None and held.symbol != symbol:
                continue
            if held.twin is not None:
                twins[held.symbol] = held.twin
            elif held.price is None:
                unpriced.add((held.symbol, dated.pool.name))
    lines.extend(
        f"[dim]{line} priced from {twin} at each day's USDCAD rate.[/dim]"
        for line, twin in sorted(twins.items())
    )
    lines.extend(
        f"[yellow]{WARN_GLYPH}{_UNPRICED_MARK} Unpriced, left out of every value: "
        f"{held} in {account}[/yellow]"
        for held, account in sorted(unpriced)
    )
    return lines
