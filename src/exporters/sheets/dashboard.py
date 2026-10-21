"""The holdings sheet: what a pool holds, at what it is worth today."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import Currency, WarningCode
from engine.panels import account_type_of
from exporters.excel_style import ACCOUNT_TYPE_TABS, FOLIO_TAB
from exporters.table import Col, Fmt, Role, Row, Table, blank_row, row_of

if TYPE_CHECKING:
    from collections.abc import Sequence
    from decimal import Decimal

    from engine.holdings import CurrencyTotals, Holding, HoldingSet
    from engine.panels import Panel, PoolView

OPEN = "Open"
CLOSED = "Closed"

_UNITS_SUSPECT = frozenset(
    {
        WarningCode.OVERSELL,
        WarningCode.NEGATIVE_FINAL_POSITION,
    },
)

_WEIGHT = "Wt%"
POOL = "Pool"

COLUMNS: tuple[Col, ...] = (
    Col("Symbol"),
    Col("Name", width=18),
    Col("Sector", width=16),
    Col("$", width=6),
    Col("Status", width=8),
    Col("Units", Fmt.UNITS),
    Col("Avg", Fmt.PRICE),
    Col("Last", Fmt.PRICE),
    Col("Change", Fmt.PRICE_SIGNED),
    Col("Change%", Fmt.PERCENT_SIGNED),
    Col("PnL", Fmt.MONEY_SIGNED),
    Col("PnL%", Fmt.PERCENT_SIGNED),
    Col("Unreal", Fmt.MONEY_SIGNED),
    Col("Unreal%", Fmt.PERCENT_SIGNED),
    Col("Realized", Fmt.MONEY_SIGNED),
    Col("Divs", Fmt.MONEY, quiet=True),
    Col("Total", Fmt.MONEY_SIGNED),
    Col("Total%", Fmt.PERCENT_SIGNED),
    Col("Book", Fmt.MONEY),
    Col("Market", Fmt.MONEY),
    Col(_WEIGHT, Fmt.PERCENT, width=10, bar=True),
    Col("Folio%", Fmt.PERCENT),
    Col("Flags", width=14),
)


def dashboard_table(
    holdings: HoldingSet,
    name: str,
    *,
    net_deposits: Decimal | None = None,
    tab_color: str | None = None,
    notes: Sequence[str] = (),
) -> Table:
    """Lay one pool's valued holdings out as a table.

    Args:
        holdings: The pool's valued holdings and totals.
        name: What the sheet is called.
        net_deposits: The pool's net CAD deposits, which is what an overall
            return is measured against. Read from its `Flows`; without it the
            overall `Total%` is left blank rather than measured against a book
            value that anything sold has already left.
        tab_color: An accent for the sheet tab, as `RRGGBB`.
        notes: Lines to disclose above the ones the table derives itself, such
            as how old the prices are.

    Returns:
        The table, with the open holdings first, then the closed ones, then the
        per-currency subtotals and the overall total below a spacer.
    """
    return Table(
        name=name,
        columns=COLUMNS,
        rows=tuple(_pool_rows(COLUMNS, holdings, net_deposits, spacer=True)),
        notes=(*notes, *_notes(holdings)),
        tab_color=tab_color,
    )


def _tab_color(view: PoolView) -> str | None:
    """Accent a sheet tab the way the printed panel accents its title.

    Args:
        view: The pool the sheet reports on.

    Returns:
        The account type's colour, the folio's for a pool spanning several
        types, or None for a type with no colour of its own.
    """
    account_type = account_type_of(view)
    if account_type is None:
        return FOLIO_TAB
    return ACCOUNT_TYPE_TABS.get(account_type)


def panel_table(panel: Panel, *, notes: Sequence[str] = ()) -> Table:
    """Lay one valued pool out as a sheet of its own, named and accented for it.

    Args:
        panel: The pool, valued.
        notes: Lines to disclose above the ones the table derives itself.

    Returns:
        The table.
    """
    return dashboard_table(
        panel.holdings,
        panel.view.label,
        net_deposits=panel.flows.net_deposit_denominator,
        tab_color=_tab_color(panel.view),
        notes=notes,
    )


def pooled_dashboard_table(
    panels: Sequence[Panel],
    name: str,
    *,
    notes: Sequence[str] = (),
) -> Table:
    """Lay several pools out as one table, each row naming the pool it is in.

    Args:
        panels: The pools, valued, in the order to list them.
        name: What the table is called.
        notes: Lines to disclose above the ones the table derives itself.

    Returns:
        The table, one pool after another.
    """
    columns = (Col(POOL, width=16), *COLUMNS)
    rows: list[Row] = []
    derived: list[str] = []
    for panel in panels:
        rows.extend(
            _pool_rows(
                columns,
                panel.holdings,
                panel.flows.net_deposit_denominator,
                pool=panel.view.label,
                spacer=False,
            ),
        )
        derived.extend(note for note in _notes(panel.holdings) if note not in derived)
    return Table(name=name, columns=columns, rows=tuple(rows), notes=(*notes, *derived))


def _pool_rows(
    columns: Sequence[Col],
    holdings: HoldingSet,
    net_deposits: Decimal | None,
    *,
    pool: str | None = None,
    spacer: bool,
) -> list[Row]:
    """Lay one pool out: its holdings, then what they total to."""
    rows = [
        row_of(columns, _named(cells, pool), role)
        for cells, role in _holding_cells(holdings)
    ]
    if rows and spacer:
        rows.append(blank_row(columns))
    for group in holdings.by_currency:
        total = _subtotal_return(holdings, group, net_deposits)
        cells = _named(_subtotal_cells(group, total), pool)
        rows.append(row_of(columns, cells, Role.SUBTOTAL))
    if holdings.mixed_currency:
        rows.append(
            row_of(
                columns,
                _named(_total_cells(holdings, net_deposits), pool),
                Role.TOTAL,
            ),
        )
    return rows


def _named(cells: dict[str, object], pool: str | None) -> dict[str, object]:
    """Say which pool a row belongs to, where the table reports several."""
    if pool is None:
        return cells
    return {POOL: pool, **cells}


def _holding_cells(
    holdings: HoldingSet,
) -> list[tuple[dict[str, object], Role]]:
    """Order every holding the pool holds, or used to hold.

    Grouped by currency the way the subtotals below them are, open holdings
    first within each group and in the order the set was sorted in. The sort is
    stable and falls back on the pool's own order, so a currency the subtotals
    somehow do not name still has its holdings written rather than dropped.
    """
    order = {group.currency: index for index, group in enumerate(holdings.by_currency)}
    last = len(order)
    entries: list[tuple[Holding, dict[str, object], Role]] = [
        (holding, _open_cells(holding), Role.DATA) for holding in holdings.holdings
    ]
    entries.extend(
        (holding, _closed_cells(holding), Role.CLOSED) for holding in holdings.closed
    )
    entries.sort(key=lambda entry: order.get(entry[0].currency, last))
    return [(cells, role) for _holding, cells, role in entries]


def _open_cells(holding: Holding) -> dict[str, object]:
    """Read one open holding into its cells."""
    return {
        "Symbol": holding.symbol,
        "Name": holding.name,
        "Sector": holding.sector,
        "$": str(holding.currency),
        "Status": OPEN,
        "Units": holding.units,
        "Avg": holding.avg_cost,
        "Last": holding.price,
        "Change": holding.change,
        "Change%": holding.change_pct,
        "PnL": holding.day_pnl,
        "PnL%": holding.day_pnl_pct,
        "Unreal": holding.unrealized,
        "Unreal%": holding.unrealized_pct,
        "Realized": holding.realized,
        "Divs": holding.dividends,
        "Total": holding.total_pnl,
        "Total%": holding.total_pnl_pct,
        "Book": holding.book_value,
        "Market": holding.market_value,
        _WEIGHT: holding.weight_in_pool,
        "Folio%": holding.weight_in_folio,
        "Flags": _flags(holding),
    }


def _closed_cells(holding: Holding) -> dict[str, object]:
    """Read one closed holding, blanking every column that no longer applies.

    Units, cost and market value are all zero on a closed holding, and printing
    those zeros would have it read as something still held. What it banked is
    real, so realized gains and dividends stay.
    """
    return {
        "Symbol": holding.symbol,
        "Name": holding.name,
        "Sector": holding.sector,
        "$": str(holding.currency),
        "Status": CLOSED,
        "Realized": holding.realized,
        "Divs": holding.dividends,
        "Total": holding.closed_total,
        "Flags": _flags(holding),
    }


def _subtotal_cells(group: CurrencyTotals, total: Decimal | None) -> dict[str, object]:
    """Subtotal one currency group, in that group's own currency."""
    return {
        "Symbol": f"{group.count} held",
        "$": str(group.currency),
        "PnL": group.day_pnl,
        "PnL%": group.day_pnl_pct,
        "Unreal": group.unrealized,
        "Unreal%": group.unrealized_pct,
        "Realized": group.realized,
        "Divs": group.dividends,
        "Total": group.total_pnl,
        "Total%": total,
        "Book": group.book,
        "Market": group.market,
        _WEIGHT: group.weight_in_pool,
        "Folio%": group.weight_in_folio,
    }


def _total_cells(
    holdings: HoldingSet,
    net_deposits: Decimal | None,
) -> dict[str, object]:
    """Total across every currency, converted into the totals currency."""
    return {
        "Symbol": "Total",
        "$": f"({holdings.totals_currency})",
        "PnL": holdings.total_day_pnl,
        "PnL%": holdings.total_day_pnl_pct,
        "Unreal": holdings.total_unrealized,
        "Unreal%": holdings.total_unrealized_pct,
        "Realized": holdings.total_realized,
        "Divs": holdings.total_dividends,
        "Total": holdings.total_pnl,
        # Against net deposits rather than book value: see `HoldingSet.return_on`.
        "Total%": holdings.return_on(net_deposits),
        "Book": holdings.total_book,
        "Market": holdings.total_market,
        "Folio%": holdings.total_weight_in_folio,
    }


def _subtotal_return(
    holdings: HoldingSet,
    group: CurrencyTotals,
    net_deposits: Decimal | None,
) -> Decimal | None:
    """Decide whether a currency subtotal is really the pool's overall total.

    The same rule the printed table follows: a single-currency pool has one
    deposit history, so its subtotal is promoted to the deposit-based return.
    """
    if holdings.mixed_currency:
        return group.total_pnl_pct
    if not holdings.deposits_measurable:
        return None
    promoted = holdings.return_on(net_deposits)
    return group.total_pnl_pct if promoted is None else promoted


def _flags(holding: Holding) -> str:
    """Spell one holding's diagnostics as a filterable list."""
    return ",".join(str(code) for code in holding.flags)


def _notes(holdings: HoldingSet) -> list[str]:
    """Disclose what the table could not show, and what it converted at."""
    notes: list[str] = []
    if holdings.unpriced:
        listed = ", ".join(holdings.unpriced)
        notes.append(
            f"{len(holdings.unpriced)} holding(s) unpriced and excluded from "
            f"totals: {listed}",
        )
    if not holdings.deposits_measurable:
        notes.append(
            f"Total% is blank: it measures the pool against its net deposits, "
            f"and {holdings.excluded} CAD holding(s) are hidden by the "
            f"{holdings.totals_currency} currency filter.",
        )
    converts = holdings.mixed_currency or holdings.display_currency is Currency.CAD
    if holdings.fx_rate is not None and converts:
        notes.append(
            f"Converted at USDCAD {float(holdings.fx_rate):,.4f} "
            f"({holdings.fx_date}). Cost base keeps its own historical rates.",
        )
    suspect = [code for code in holdings.flags if code in _UNITS_SUSPECT]
    if suspect:
        codes = ", ".join(str(code) for code in suspect)
        notes.append(
            f"Flagged holdings carry {codes}; their units are known to be "
            f"wrong. Run `folio check`.",
        )
    return notes
