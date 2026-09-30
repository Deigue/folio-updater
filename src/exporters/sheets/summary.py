"""The summary sheets: the whole folio at a glance, and every pool beside it.

Every figure is read off the same panels the dashboard sheets are laid out
from. Nothing is computed here, only arranged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import Currency, Scope
from exporters.excel_style import OVERVIEW_TAB
from exporters.table import Block, Col, Fmt, Line, Link, Table, row_of

if TYPE_CHECKING:
    from collections.abc import Sequence

    from engine.flows import Flows
    from engine.panels import Panel

# How each grain reads in a `Scope` column.
SCOPE_NAMES: dict[Scope, str] = {
    Scope.FOLIO: "Portfolio",
    Scope.TYPE: "Type",
    Scope.ACCOUNT: "Account",
}

PERFORMANCE_COLUMNS: tuple[Col, ...] = (
    Col("Pool", width=18),
    Col("Scope", width=10),
    Col("Held", Fmt.ID),
    Col("Book", Fmt.MONEY),
    Col("Market", Fmt.MONEY),
    Col("Unreal", Fmt.MONEY_SIGNED),
    Col("Unreal%", Fmt.PERCENT_SIGNED),
    Col("Realized", Fmt.MONEY_SIGNED),
    Col("Divs", Fmt.MONEY, quiet=True),
    Col("Total", Fmt.MONEY_SIGNED),
    Col("Total%", Fmt.PERCENT_SIGNED),
    Col("Folio%", Fmt.PERCENT, bar=True),
)

FLOW_COLUMNS: tuple[Col, ...] = (
    Col("Pool", width=18),
    Col("Scope", width=10),
    Col("$", width=6),
    Col("Cash", Fmt.MONEY_SIGNED),
    Col("Contributions", Fmt.MONEY, quiet=True),
    Col("Withdrawn", Fmt.MONEY, quiet=True),
    Col("Transferred", Fmt.MONEY_SIGNED),
    Col("Net Deposited", Fmt.MONEY),
    Col("Dividends", Fmt.MONEY, quiet=True),
    Col("Fees", Fmt.MONEY, quiet=True),
    Col("Realized", Fmt.MONEY_SIGNED),
    Col("Room Year", Fmt.ID),
    Col("Room Used", Fmt.MONEY),
    Col("Room Limit", Fmt.MONEY),
    Col("Room Left", Fmt.MONEY_SIGNED),
)

_FROZEN = 2  # `Pool` and `Scope` say which row is which


def summary_table(
    folio: Panel,
    pools: Sequence[Panel],
    *,
    name: str = "Summary",
    notes: Sequence[str] = (),
    contents: Sequence[tuple[str, str]] = (),
) -> Table:
    """Table for the folio's headline figures out, with every pool's beside them.

    Each pool in the table links to that pool's own sheet, and a panel
    links everything else.

    Args:
        folio: The portfolio-wide panel, which the headline block reads.
        pools: Every pool to compare, portfolio first, in the order to list.
        name: What the sheet is called.
        notes: Lines to disclose under the table, such as how old the prices
            are.
        contents: The other sheets to link, each with a line saying what it
            holds.

    Returns:
        The table, with the headline and contents panels above it.
    """
    blocks = [_headline(folio)]
    if contents:
        blocks.append(_contents(contents))
    return Table(
        name=name,
        columns=PERFORMANCE_COLUMNS,
        rows=tuple(
            row_of(PERFORMANCE_COLUMNS, _performance_cells(pool)) for pool in pools
        ),
        blocks=tuple(blocks),
        notes=tuple(notes),
        tab_color=OVERVIEW_TAB,
        freeze=_FROZEN,
    )


def flows_table(
    pools: Sequence[Panel],
    *,
    name: str = "Flows",
    notes: Sequence[str] = (),
) -> Table:
    """Flows table representing cash that moved through every pool.

    Args:
        pools: Every pool to list, portfolio first.
        name: What the sheet is called.
        notes: Lines to disclose under the table.

    Returns:
        The table, one row per pool and currency.
    """
    return Table(
        name=name,
        columns=FLOW_COLUMNS,
        rows=tuple(
            row_of(FLOW_COLUMNS, cells) for pool in pools for cells in _flow_cells(pool)
        ),
        notes=tuple(notes),
        tab_color=OVERVIEW_TAB,
        freeze=_FROZEN + 1,
    )


def _headline(folio: Panel) -> Block:
    """Read the portfolio's headline figures into a block."""
    holdings = folio.holdings
    flows = folio.flows
    base = str(holdings.base_currency)
    lines = [
        Line("Market value", holdings.total_market, Fmt.MONEY, base),
        Line("Book value", holdings.total_book, Fmt.MONEY, base),
        Line("Unrealized", holdings.total_unrealized, Fmt.MONEY_SIGNED, base),
        Line("Realized", holdings.total_realized, Fmt.MONEY_SIGNED, base),
        Line("Dividends", holdings.total_dividends, Fmt.MONEY, base),
        Line("Total earned", holdings.total_pnl, Fmt.MONEY_SIGNED, base),
        Line(
            "Total return",
            holdings.return_on(flows.net_deposit_denominator),
            Fmt.PERCENT_SIGNED,
            "on net deposits",
        ),
        Line(
            "Net deposited",
            flows.net_deposited.get(Currency.CAD),
            Fmt.MONEY,
            str(Currency.CAD),
        ),
    ]
    lines.extend(
        Line("Cash", amount, Fmt.MONEY_SIGNED, str(currency))
        for currency, amount in sorted(
            flows.cash.items(),
            key=lambda item: str(item[0]),
        )
    )
    return Block(title="Portfolio", lines=tuple(lines))


def _contents(sheets: Sequence[tuple[str, str]]) -> Block:
    """Link every sheet the pool table does not, with what each one holds."""
    return Block(
        title="Sheets",
        lines=tuple(
            Line(Link(sheet, sheet), note=description) for sheet, description in sheets
        ),
    )


def _pool(pool: Panel) -> Link:
    """Name a pool, as a link to its own sheet."""
    return Link(pool.view.label, pool.view.label)


def _performance_cells(pool: Panel) -> dict[str, object]:
    """Read one pool's totals, in the base currency every pool shares."""
    holdings = pool.holdings
    return {
        "Pool": _pool(pool),
        "Scope": SCOPE_NAMES[pool.view.scope],
        "Held": len(holdings.holdings),
        "Book": holdings.total_book,
        "Market": holdings.total_market,
        "Unreal": holdings.total_unrealized,
        "Unreal%": holdings.total_unrealized_pct,
        "Realized": holdings.total_realized,
        "Divs": holdings.total_dividends,
        "Total": holdings.total_pnl,
        # Against net deposits rather than book value: see `HoldingSet.return_on`.
        "Total%": holdings.return_on(pool.flows.net_deposit_denominator),
        "Folio%": holdings.total_weight_in_folio,
    }


def _flow_cells(pool: Panel) -> list[dict[str, object]]:
    """Read one pool's cash movements, one set of cells per currency."""
    flows = pool.flows
    rows: list[dict[str, object]] = []
    for currency in _currencies(flows):
        cells: dict[str, object] = {
            "Pool": _pool(pool),
            "Scope": SCOPE_NAMES[pool.view.scope],
            "$": str(currency),
            "Cash": flows.cash.get(currency),
            "Contributions": flows.contributions.get(currency),
            "Withdrawn": flows.withdrawals.get(currency),
            "Transferred": flows.transfers.get(currency),
            "Net Deposited": flows.net_deposited.get(currency),
            "Dividends": flows.dividends.get(currency),
            "Fees": flows.fees.get(currency),
            "Realized": flows.realized.get(currency),
        }
        # Contribution room is a CAD limit, so it belongs on the CAD line.
        room = flows.room
        if room is not None and currency is Currency.CAD:
            cells.update(
                {
                    "Room Year": room.year,
                    "Room Used": room.used,
                    "Room Limit": room.limit,
                    "Room Left": room.remaining,
                },
            )
        rows.append(cells)
    return rows


def _currencies(flows: Flows) -> list[Currency]:
    """Every currency the pool moved, CAD first."""
    seen = (
        set(flows.cash)
        | set(flows.contributions)
        | set(flows.withdrawals)
        | set(flows.transfers)
        | set(flows.dividends)
        | set(flows.fees)
        | set(flows.realized)
    )
    return sorted(
        seen,
        key=lambda currency: (currency is not Currency.CAD, str(currency)),
    )
