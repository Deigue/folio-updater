"""The holdings sheet: what a pool holds, at what it is worth today."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import Currency, WarningCode
from exporters.table import Col, Fmt, Role, Table, blank_row, row_of

if TYPE_CHECKING:
    from collections.abc import Sequence
    from decimal import Decimal

    from engine.positions import CurrencyTotals, Holding, HoldingSet

OPEN = "Open"
CLOSED = "Closed"

_UNITS_SUSPECT = frozenset(
    {
        WarningCode.OVERSELL,
        WarningCode.NEGATIVE_FINAL_POSITION,
    },
)

_WEIGHT = "Wt%"

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
    Col("Divs", Fmt.MONEY_QUIET),
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
    net_deposited: Decimal | None = None,
    tab_color: str | None = None,
    notes: Sequence[str] = (),
) -> Table:
    """Lay one pool's valued positions out as a table.

    Args:
        holdings: The pool's valued positions and totals.
        name: What the sheet is called.
        net_deposited: The pool's net CAD deposits, which is what an overall
            return is measured against. Read from its `Flows`; without it the
            overall `Total%` is left blank rather than measured against a book
            value that anything sold has already left.
        tab_color: An accent for the sheet tab, as `RRGGBB`.
        notes: Lines to disclose above the ones the table derives itself, such
            as how old the prices are.

    Returns:
        The table, with the open positions first, then the closed ones, then the
        per-currency subtotals and the overall total below a spacer.
    """
    rows = [row_of(COLUMNS, cells, role) for cells, role in _position_cells(holdings)]
    if rows:
        rows.append(blank_row(COLUMNS))
    rows.extend(
        row_of(
            COLUMNS,
            _subtotal_cells(group, _subtotal_return(holdings, group, net_deposited)),
            Role.SUBTOTAL,
        )
        for group in holdings.by_currency
    )
    if holdings.mixed_currency:
        rows.append(
            row_of(COLUMNS, _total_cells(holdings, net_deposited), Role.TOTAL),
        )

    return Table(
        name=name,
        columns=COLUMNS,
        rows=tuple(rows),
        notes=(*notes, *_notes(holdings)),
        tab_color=tab_color,
    )


def _position_cells(
    holdings: HoldingSet,
) -> list[tuple[dict[str, object], Role]]:
    """Order every position the pool holds, or used to hold.

    Grouped by currency the way the subtotals below them are, open positions
    first within each group and in the order the set was sorted in. The sort is
    stable and falls back on the pool's own order, so a currency the subtotals
    somehow do not name still has its positions written rather than dropped.
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
    """Read one open position into its cells."""
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
    """Read one sold-out position, blanking every column that no longer applies.

    Units, cost and market value are all zero on a closed position, and printing
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
    net_deposited: Decimal | None,
) -> dict[str, object]:
    """Total across every currency, converted into the base currency."""
    return {
        "Symbol": "Total",
        "$": f"({holdings.base_currency})",
        "PnL": holdings.total_day_pnl,
        "PnL%": holdings.total_day_pnl_pct,
        "Unreal": holdings.total_unrealized,
        "Unreal%": holdings.total_unrealized_pct,
        "Realized": holdings.total_realized,
        "Divs": holdings.total_dividends,
        "Total": holdings.total_pnl,
        # Against net deposits rather than book value: see `HoldingSet.return_on`.
        "Total%": holdings.return_on(net_deposited),
        "Book": holdings.total_book,
        "Market": holdings.total_market,
        "Folio%": holdings.total_weight_in_folio,
    }


def _subtotal_return(
    holdings: HoldingSet,
    group: CurrencyTotals,
    net_deposited: Decimal | None,
) -> Decimal | None:
    """Decide whether a currency subtotal is really the pool's overall total.

    The same rule the printed table follows: a single-currency pool has one
    deposit history, so its subtotal is promoted to the deposit-based return.
    """
    if holdings.mixed_currency:
        return group.total_pnl_pct
    if not holdings.deposits_measurable:
        return None
    promoted = holdings.return_on(net_deposited)
    return group.total_pnl_pct if promoted is None else promoted


def _flags(holding: Holding) -> str:
    """Spell one position's diagnostics as a filterable list."""
    return ",".join(str(code) for code in holding.flags)


def _notes(holdings: HoldingSet) -> list[str]:
    """Disclose what the table could not show, and what it converted at."""
    notes: list[str] = []
    if holdings.unpriced:
        listed = ", ".join(holdings.unpriced)
        notes.append(
            f"{len(holdings.unpriced)} position(s) unpriced and excluded from "
            f"totals: {listed}",
        )
    if not holdings.deposits_measurable:
        notes.append(
            f"Total% is blank: it measures the pool against its net deposits, "
            f"and {holdings.excluded} CAD position(s) are hidden by the "
            f"{holdings.base_currency} currency filter.",
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
            f"Flagged positions carry {codes}; their units are known to be "
            f"wrong. Run `folio check`.",
        )
    return notes
