"""Every figure a table can show, defined once for the terminal and the sheets."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from domain import Fmt, Measure
from domain.numeric import ZERO
from exporters.sheet_spec import Col, read_cells

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from engine.flows import FlowLine
    from engine.holdings import Holding
    from models import Quote

_REFERENCE = Measure.REFERENCE


# Every column a holding's row can show, in the dashboard's order. A table picks
# its own columns from these by key; the keys are the names `--sort` takes.
HOLDING_COLUMNS: tuple[Col[Holding], ...] = (
    Col("Symbol", key="symbol", read=lambda h: h.symbol),
    Col("Name", width=18, key="name", read=lambda h: h.name),
    Col("Sector", width=16, key="sector", read=lambda h: h.sector),
    Col(
        "$",
        width=6,
        key="currency",
        read=lambda h: str(h.currency),
    ),
    Col("Units", Fmt.UNITS, key="units", read=lambda h: h.units, measure=_REFERENCE),
    Col("Avg", Fmt.PRICE, key="avg", read=lambda h: h.avg_cost, measure=_REFERENCE),
    Col("Last", Fmt.PRICE, key="last", read=lambda h: h.price),
    Col("Change", Fmt.PRICE_SIGNED, key="change", read=lambda h: h.change),
    Col(
        "Change%",
        Fmt.PERCENT_SIGNED,
        key="change%",
        read=lambda h: h.change_pct,
        measure=Measure.DAY_MOVE,
    ),
    Col("PnL", Fmt.MONEY_SIGNED, key="pnl", read=lambda h: h.day_pnl),
    Col(
        "PnL%",
        Fmt.PERCENT_SIGNED,
        key="pnl%",
        read=lambda h: h.day_pnl_pct,
        measure=Measure.DAY_MOVE,
    ),
    Col("Unreal", Fmt.MONEY_SIGNED, key="unreal", read=lambda h: h.unrealized),
    Col(
        "Unreal%",
        Fmt.PERCENT_SIGNED,
        key="unreal%",
        read=lambda h: h.unrealized_pct,
        coarse=True,
        measure=Measure.RETURN,
    ),
    Col(
        "Realized",
        Fmt.MONEY_SIGNED,
        quiet=True,
        key="realized",
        read=lambda h: h.realized,
    ),
    Col(
        "Divs",
        Fmt.MONEY,
        quiet=True,
        key="divs",
        read=lambda h: h.dividends,
        measure=Measure.INCOME,
    ),
    Col(
        "Total",
        Fmt.MONEY_SIGNED,
        key="total",
        read=lambda h: h.total_pnl,
    ),
    Col(
        "Total%",
        Fmt.PERCENT_SIGNED,
        key="total%",
        read=lambda h: h.total_pnl_pct,
        coarse=True,
        measure=Measure.RETURN,
    ),
    Col("Book", Fmt.MONEY, key="book", read=lambda h: h.book_value, measure=_REFERENCE),
    Col("Market", Fmt.MONEY, key="market", read=lambda h: h.market_value),
    Col(
        "Wt%",
        Fmt.PERCENT,
        key="wt%",
        read=lambda h: h.weight_in_pool,
        coarse=True,
        measure=Measure.WEIGHT,
    ),
    Col(
        "Folio%",
        Fmt.PERCENT,
        key="folio%",
        read=lambda h: h.weight_in_folio,
        coarse=True,
        measure=Measure.WEIGHT,
    ),
)

HOLDINGS_BY_KEY: Mapping[str, Col[Holding]] = MappingProxyType(
    {column.key: column for column in HOLDING_COLUMNS},
)


# The columns a closed holding still fills: what it is, and what it banked.
# Units, cost and market value fall to zero once sold out, and printing those
# zeros would have the holding read as still held.
CLOSED_KEEPS = frozenset(
    {"symbol", "name", "sector", "currency", "realized", "divs", "total"},
)


def shows(column: Col[Holding], holding: Holding) -> bool:
    """Whether a holding's row fills a column: an open one fills every column."""
    return not holding.closed or column.key in CLOSED_KEEPS


def holding_cells(
    columns: Sequence[Col[Holding]],
    holding: Holding,
) -> dict[str, object]:
    """Read a holding's row, keyed by heading, blank where a closed one has nothing."""
    return read_cells([column for column in columns if shows(column, holding)], holding)


def holding_columns(*keys: str) -> tuple[Col[Holding], ...]:
    """Pick holding columns by key, in the order given."""
    return tuple(HOLDINGS_BY_KEY[key] for key in keys)


def pool_columns(share: str) -> tuple[Col[Holding], ...]:
    """Pick the columns a table of one security's pools shows, after each pool's name.

    `share` heads the weight in the pool: the holding's share of its account, or
    of its account type.
    """
    return tuple(
        replace(column, header=share) if column.key == "wt%" else column
        for column in holding_columns(
            "units",
            "avg",
            "book",
            "market",
            "unreal",
            "unreal%",
            "realized",
            "divs",
            "total",
            "total%",
            "wt%",
            "folio%",
        )
    )


VALUATION = "Valuation"
DIVIDENDS = "Dividends"
TRADING = "Trading"
FUND = "Fund"

# Each fundamental, in the order a table comparing securities lists them: what
# compares across securities first, a fund's own figures last.
FUNDAMENTALS: tuple[Col[Quote], ...] = (
    Col(
        "Market cap",
        Fmt.LARGE,
        VALUATION,
        key="market_cap",
        read=lambda q: q.market_cap,
    ),
    Col(
        "P/E",
        Fmt.RATIO,
        VALUATION,
        key="pe",
        read=lambda q: q.fundamentals.trailing_pe,
    ),
    Col(
        "Fwd P/E",
        Fmt.RATIO,
        VALUATION,
        key="forward_pe",
        label="Forward P/E",
        read=lambda q: q.fundamentals.forward_pe,
    ),
    Col("EPS", Fmt.PRICE, VALUATION, key="eps", read=lambda q: q.fundamentals.eps),
    Col("Beta", Fmt.RATIO, VALUATION, key="beta", read=lambda q: q.fundamentals.beta),
    Col(
        "Earnings",
        Fmt.DATE,
        VALUATION,
        key="earnings",
        read=lambda q: q.fundamentals.earnings_date,
    ),
    Col(
        "Yield",
        Fmt.PERCENT,
        DIVIDENDS,
        key="yield",
        read=lambda q: q.fundamentals.dividend_yield,
    ),
    Col(
        "Dividend",
        Fmt.PRICE,
        DIVIDENDS,
        key="dividend",
        read=lambda q: q.fundamentals.dividend_rate,
    ),
    Col(
        "Last paid",
        Fmt.PRICE,
        DIVIDENDS,
        key="last_dividend",
        label="Last dividend",
        read=lambda q: q.fundamentals.last_dividend,
    ),
    Col(
        "Ex-dividend",
        Fmt.DATE,
        DIVIDENDS,
        key="ex_dividend",
        read=lambda q: q.fundamentals.ex_dividend_date,
    ),
    Col(
        "52w low",
        Fmt.PRICE,
        TRADING,
        key="low_52",
        label="52-week low",
        read=lambda q: q.fundamentals.low_52,
    ),
    Col(
        "52w high",
        Fmt.PRICE,
        TRADING,
        key="high_52",
        label="52-week high",
        read=lambda q: q.fundamentals.high_52,
    ),
    Col(
        "50d avg",
        Fmt.PRICE,
        TRADING,
        key="avg_50",
        label="50-day avg",
        read=lambda q: q.fundamentals.avg_50,
    ),
    Col(
        "200d avg",
        Fmt.PRICE,
        TRADING,
        key="avg_200",
        label="200-day avg",
        read=lambda q: q.fundamentals.avg_200,
    ),
    Col(
        "Volume",
        Fmt.COUNT,
        TRADING,
        key="volume",
        read=lambda q: q.fundamentals.volume,
    ),
    Col(
        "Avg volume",
        Fmt.COUNT,
        TRADING,
        key="avg_volume",
        read=lambda q: q.fundamentals.avg_volume,
    ),
    Col(
        "Expense",
        Fmt.PERCENT,
        FUND,
        key="expense_ratio",
        label="Expense ratio",
        read=lambda q: q.fundamentals.expense_ratio,
    ),
    Col(
        "Assets",
        Fmt.LARGE,
        FUND,
        key="assets",
        read=lambda q: q.fundamentals.total_assets,
    ),
    Col(
        "Category",
        Fmt.TEXT,
        FUND,
        width=18,
        key="category",
        read=lambda q: q.fundamentals.category,
    ),
    Col(
        "Family",
        Fmt.TEXT,
        FUND,
        width=22,
        key="family",
        read=lambda q: q.fundamentals.fund_family,
    ),
)

FUNDAMENTALS_BY_KEY: Mapping[str, Col[Quote]] = MappingProxyType(
    {column.key: column for column in FUNDAMENTALS},
)

# How a list of one security's fundamentals reads, section by section. A fund's
# figures sit among the valuation ones, so a stock and a fund read the same.
FUNDAMENTAL_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        VALUATION,
        (
            "market_cap",
            "assets",
            "expense_ratio",
            "category",
            "family",
            "pe",
            "forward_pe",
            "eps",
            "beta",
            "earnings",
        ),
    ),
    (DIVIDENDS, ("yield", "dividend", "last_dividend", "ex_dividend")),
    (TRADING, ("low_52", "high_52", "avg_50", "avg_200", "volume", "avg_volume")),
)


# Every flow a pool's line can show, in a flows table's order. A missing figure
# reads as zero, which each column leaves blank.
FLOW_FIGURES: tuple[Col[FlowLine], ...] = (
    Col(
        "Contributions",
        Fmt.MONEY,
        quiet=True,
        key="contributions",
        read=lambda line: line.flows.contributions.get(line.currency, ZERO),
    ),
    Col(
        "Withdrawn",
        Fmt.MONEY,
        quiet=True,
        key="withdrawn",
        read=lambda line: line.flows.withdrawals.get(line.currency, ZERO),
    ),
    Col(
        "Transferred",
        Fmt.MONEY_SIGNED,
        quiet=True,
        key="transferred",
        read=lambda line: line.flows.transfers.get(line.currency, ZERO),
    ),
    Col(
        "Bought",
        Fmt.MONEY,
        quiet=True,
        key="bought",
        read=lambda line: line.flows.bought.get(line.currency, ZERO),
    ),
    Col(
        "Sold",
        Fmt.MONEY,
        quiet=True,
        key="sold",
        read=lambda line: line.flows.sold.get(line.currency, ZERO),
    ),
    Col(
        "Dividends",
        Fmt.MONEY,
        quiet=True,
        key="dividends",
        read=lambda line: line.flows.dividends.get(line.currency, ZERO),
    ),
    Col(
        "Fees",
        Fmt.MONEY,
        quiet=True,
        key="fees",
        read=lambda line: line.flows.fees.get(line.currency, ZERO),
    ),
    Col(
        "Realized",
        Fmt.MONEY_SIGNED,
        quiet=True,
        key="realized",
        read=lambda line: line.flows.realized.get(line.currency, ZERO),
    ),
    Col(
        "Trades",
        Fmt.TALLY,
        quiet=True,
        key="trades",
        read=lambda line: line.flows.trades.get(line.currency, 0),
    ),
)
