"""Rendering every account's market value on a past valuation date."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich import box
from rich.table import Table as RichTable

from domain import AccountType, Currency
from engine.accounts import FOLIO, account_type_of
from exporters.sheet_spec import Fmt
from term import console_print
from ui.cells import WARN_GLYPH, money, style, units
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
    from engine.past_values import DatedPool, DatedValuation

# Marks a value that leaves out an unpriced holding.
_UNPRICED_MARK = "*"


def show_value(valued: DatedValuation, into: Currency, *, capped: bool) -> None:
    """Print every account's holdings, cash and value, then what they leave out.

    Args:
        valued: Every pool on the valuation date.
        into: The currency every figure is converted into.
        capped: The day asked had not closed, so the latest close stands in.
    """
    console_print(_value_table(valued, into, capped=capped))
    for line in _disclosures(valued, into):
        console_print(line)


def _title(valued: DatedValuation, into: Currency, *, capped: bool) -> str:
    """Name the valuation date, and why it differs from the day asked."""
    note = ""
    if capped:
        note = " (latest close)"
    elif valued.on != valued.asked:
        note = f" (close before {valued.asked})"
    return f"Market value at {valued.on}{note} · {into}"


def _value_table(valued: DatedValuation, into: Currency, *, capped: bool) -> RichTable:
    """Render one row per account, grouped by account type, then the total."""
    table = RichTable(
        title=_title(valued, into, capped=capped),
        title_justify="left",
        box=box.SIMPLE,
        show_edge=False,
        header_style=TABLE_HEADER,
        border_style=TABLE_BORDER,
    )
    for column in (
        FormattedColumn("Account", no_wrap=True),
        FormattedColumn("Type", no_wrap=True),
        FormattedColumn("Holdings", fmt=Fmt.MONEY, justify="right"),
        FormattedColumn("Cash", fmt=Fmt.MONEY, justify="right"),
        FormattedColumn("Value", fmt=Fmt.MONEY, justify="right"),
    ):
        add_column(table, column)

    for _, accounts in valued.type_groups():
        for account in accounts:
            tint = ACCOUNT_TYPE_COLORS.get(account_type_of(account.pool))
            table.add_row(
                style(account.pool.name, tint),
                style(_type_label(account), tint),
                *_figures(account, into),
            )
    if transit := valued.in_transit(into):
        table.add_row("In transit", "", "", "", money(transit))
    table.add_section()
    table.add_row(
        "Total",
        "",
        *_figures(valued.pools[FOLIO], into),
        style=GRAND_TOTAL_ROW_STYLE,
    )
    return fit_table(table)


def _type_label(dated: DatedPool) -> str:
    """Name a pool's account type in four letters."""
    kind = account_type_of(dated.pool) or AccountType.UNKNOWN
    return SHORT_ACCOUNT_TYPES.get(kind, str(kind))


def _figures(dated: DatedPool, into: Currency) -> tuple[str, str, str]:
    """Render a pool's holdings, cash and value, marking a value with gaps."""
    mark = _UNPRICED_MARK if dated.unpriced else ""
    return (
        money(dated.holdings_value(into)),
        money(dated.cash_value(into)),
        f"{money(dated.market_value(into))}{mark}",
    )


def _disclosures(valued: DatedValuation, into: Currency) -> list[str]:
    """Word what the figures were converted at and what they leave out."""
    lines: list[str] = []
    folio = valued.pools[FOLIO]
    if len(folio.currencies | {into}) > 1:
        lines.append(
            f"[dim]Converted to {into} at the {valued.rate_date} Bank of Canada "
            f"rate, USDCAD {float(folio.usdcad):,.4f}.[/dim]",
        )
    for _, accounts in valued.type_groups():
        for account in accounts:
            for held in account.holdings:
                if held.twin is not None:
                    lines.append(
                        f"[dim]{held.symbol} priced from {held.twin} at the "
                        f"{valued.rate_date} rate.[/dim]",
                    )
                elif held.price is None:
                    lines.append(
                        f"[yellow]{WARN_GLYPH}{_UNPRICED_MARK} Unpriced, left out of "
                        f"every value: {held.symbol} in {account.pool.name} "
                        f"({units(held.units)} units)[/yellow]",
                    )
    return lines
