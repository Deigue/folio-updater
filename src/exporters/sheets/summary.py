"""The summary sheets: the whole folio at a glance, and every pool beside it.

Every figure is read off the same valued pools the dashboard sheets are
printed from. Nothing is computed here, only arranged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import AccountType, Currency, Grain
from exporters.excel_style import OVERVIEW_TAB
from exporters.sheet_spec import (
    Col,
    Fmt,
    Line,
    Link,
    Sheet,
    SheetList,
    SheetTable,
    row_of,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from engine.flows import Flows, Room
    from engine.pools import ValuedPool

# How each grain reads in a `Grain` column.
GRAIN_NAMES: dict[Grain, str] = {
    Grain.FOLIO: "Portfolio",
    Grain.TYPE: "Type",
    Grain.ACCOUNT: "Account",
}

SUMMARY_SHEET = "Summary"
FLOWS_SHEET = "Flows"

POOL_COLUMNS: tuple[Col, ...] = (
    Col("Pool", width=18),
    Col("Grain", width=10),
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
    Col("Grain", width=10),
    Col("$", width=6),
    Col("Cash", Fmt.MONEY_SIGNED),
    Col("Contributions", Fmt.MONEY, quiet=True),
    Col("Withdrawn", Fmt.MONEY, quiet=True),
    Col("Transferred", Fmt.MONEY_SIGNED),
    Col("Net Deposits", Fmt.MONEY),
    Col("Dividends", Fmt.MONEY, quiet=True),
    Col("Fees", Fmt.MONEY, quiet=True),
    Col("Realized", Fmt.MONEY_SIGNED),
    Col("Room Year", Fmt.ID),
    Col("Room Used", Fmt.MONEY),
    Col("Room Limit", Fmt.MONEY),
    Col("Room Left", Fmt.MONEY_SIGNED),
)

HISTORY_COLUMNS: tuple[Col, ...] = (
    Col("Year", Fmt.ID),
    Col("Type", width=10),
    Col("Contributed", Fmt.MONEY),
    Col("Withdrawn", Fmt.MONEY, quiet=True),
    Col("Limit", Fmt.MONEY),
    Col("Used%", Fmt.PERCENT),
    Col("Left", Fmt.MONEY_SIGNED),
)

_RRSP_NOTE = (
    "RRSP contributions made in the first 60 days of a year count toward the "
    "year before, as CRA counts them."
)

_FROZEN = 2  # `Pool` and `Grain` say which row is which


def summary_sheet(
    folio: ValuedPool,
    valued_pools: Sequence[ValuedPool],
    *,
    name: str = SUMMARY_SHEET,
    notes: Sequence[str] = (),
    contents: Sequence[tuple[str, str]] = (),
) -> Sheet:
    """Print the folio's headline figures as a sheet, with every pool's below them.

    Each pool in the table links to that pool's own sheet, and a list links
    everything else.

    Args:
        folio: The folio pool, valued, which the headline list reads.
        valued_pools: Every pool to compare, folio first, in the order to list.
        name: What the sheet is called.
        notes: Lines to disclose under the table, such as how old the prices
            are.
        contents: The other sheets to link, each with a line saying what it
            holds.

    Returns:
        The sheet, with the headline and contents lists above its table.
    """
    lists = [_headline_list(folio)]
    if contents:
        lists.append(_contents_list(contents))
    return Sheet(
        name=name,
        tables=(
            SheetTable(
                POOL_COLUMNS,
                tuple(
                    row_of(POOL_COLUMNS, _pool_cells(valued)) for valued in valued_pools
                ),
                notes=tuple(notes),
            ),
        ),
        lists=tuple(lists),
        tab_color=OVERVIEW_TAB,
        freeze=_FROZEN,
    )


def flows_sheet(
    valued_pools: Sequence[ValuedPool],
    *,
    name: str = FLOWS_SHEET,
    notes: Sequence[str] = (),
    history: Sequence[Room] = (),
) -> Sheet:
    """Print the cash that moved through every pool as a sheet.

    Args:
        valued_pools: Every pool to list, folio first.
        name: What the sheet is called.
        notes: Lines to disclose under the table.
        history: Every year of contribution room used, newest first, listed
            under the flows as a table of its own.

    Returns:
        The sheet, one row per pool and currency, then any history.
    """
    flows = SheetTable(
        FLOW_COLUMNS,
        tuple(
            row_of(FLOW_COLUMNS, cells)
            for valued in valued_pools
            for cells in _flow_cells(valued)
        ),
        notes=tuple(notes),
    )
    return Sheet(
        name=name,
        tables=(flows, history_table(history)) if history else (flows,),
        tab_color=OVERVIEW_TAB,
        freeze=_FROZEN + 1,
    )


def history_table(
    history: Sequence[Room],
    *,
    title: str = "Contribution history",
) -> SheetTable:
    """List what went into every account type having contribution limits by year.

    Args:
        history: One `Room` per type and year, in the order to list.
        title: The table's title.

    Returns:
        The table, one row per type and year. Limit, used and left stay blank
        for a year with no configured limit rather than reading as zero.
    """
    rrsp = any(room.account_type is AccountType.RRSP for room in history)
    return SheetTable(
        HISTORY_COLUMNS,
        tuple(row_of(HISTORY_COLUMNS, _history_cells(room)) for room in history),
        notes=(_RRSP_NOTE,) if rrsp else (),
        title=title,
    )


def _headline_list(folio: ValuedPool) -> SheetList:
    """Read the folio's headline figures into a list."""
    holdings = folio.holdings
    flows = folio.flows
    totals = str(holdings.totals_currency)
    lines = [
        Line("Market value", holdings.total_market, Fmt.MONEY, totals),
        Line("Book value", holdings.total_book, Fmt.MONEY, totals),
        Line("Unrealized", holdings.total_unrealized, Fmt.MONEY_SIGNED, totals),
        Line("Realized", holdings.total_realized, Fmt.MONEY_SIGNED, totals),
        Line("Dividends", holdings.total_dividends, Fmt.MONEY, totals),
        Line("Total gain", holdings.total_pnl, Fmt.MONEY_SIGNED, totals),
        Line(
            "Total return",
            folio.total_return.ratio,
            Fmt.PERCENT_SIGNED,
            "on net deposits",
        ),
        Line(
            "Net Deposits",
            flows.net_deposits.get(Currency.CAD),
            Fmt.MONEY,
            str(Currency.CAD),
        ),
    ]
    # CAD is always listed; another currency only when the folio deposited in it.
    lines.extend(
        Line("Net Deposits", amount, Fmt.MONEY, str(currency))
        for currency, amount in sorted(
            flows.net_deposits.items(),
            key=lambda item: str(item[0]),
        )
        if currency is not Currency.CAD
    )
    lines.extend(
        Line("Cash", amount, Fmt.MONEY_SIGNED, str(currency))
        for currency, amount in sorted(
            flows.cash.items(),
            key=lambda item: str(item[0]),
        )
    )
    return SheetList(title="Portfolio", lines=tuple(lines))


def _contents_list(sheets: Sequence[tuple[str, str]]) -> SheetList:
    """Link every sheet the pool table does not, with what each one holds."""
    return SheetList(
        title="Sheets",
        lines=tuple(
            Line(Link(sheet, sheet), note=description) for sheet, description in sheets
        ),
    )


def _pool(valued: ValuedPool) -> Link:
    """Name a pool, as a link to its own sheet."""
    return Link(valued.label, valued.label)


def _pool_cells(valued: ValuedPool) -> dict[str, object]:
    """Read one pool's totals, in the totals currency every pool shares."""
    holdings = valued.holdings
    return {
        "Pool": _pool(valued),
        "Grain": GRAIN_NAMES[valued.grain],
        "Held": len(holdings.holdings),
        "Book": holdings.total_book,
        "Market": holdings.total_market,
        "Unreal": holdings.total_unrealized,
        "Unreal%": holdings.total_unrealized_pct,
        "Realized": holdings.total_realized,
        "Divs": holdings.total_dividends,
        "Total": holdings.total_pnl,
        "Total%": valued.total_return.ratio,
        "Folio%": holdings.total_weight_in_folio,
    }


def _flow_cells(valued: ValuedPool) -> list[dict[str, object]]:
    """Read one pool's cash movements, one set of cells per currency."""
    flows = valued.flows
    rows: list[dict[str, object]] = []
    for currency in _currencies(flows):
        cells: dict[str, object] = {
            "Pool": _pool(valued),
            "Grain": GRAIN_NAMES[valued.grain],
            "$": str(currency),
            "Cash": flows.cash.get(currency),
            "Contributions": flows.contributions.get(currency),
            "Withdrawn": flows.withdrawals.get(currency),
            "Transferred": flows.transfers.get(currency),
            "Net Deposits": flows.net_deposits.get(currency),
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


def _history_cells(room: Room) -> dict[str, object]:
    """Read one type's year of contribution room."""
    return {
        "Year": room.year,
        "Type": str(room.account_type),
        "Contributed": room.used,
        "Withdrawn": room.withdrawn,
        "Limit": room.limit,
        "Used%": room.used_ratio,
        "Left": room.remaining,
    }


def _currencies(flows: Flows) -> list[Currency]:
    """Every currency the pool moved, CAD first (and CAD whenever it has room)."""
    seen = (
        set(flows.cash)
        | set(flows.contributions)
        | set(flows.withdrawals)
        | set(flows.transfers)
        | set(flows.dividends)
        | set(flows.fees)
        | set(flows.realized)
    )
    if flows.room is not None:
        # The room is written on the CAD line, so a pool that moved only USD
        # still gets one.
        seen.add(Currency.CAD)
    return sorted(
        seen,
        key=lambda currency: (currency is not Currency.CAD, str(currency)),
    )
