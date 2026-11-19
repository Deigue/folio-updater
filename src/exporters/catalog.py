"""Every figure a table can show, defined once for the terminal and the sheets."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from exporters.sheet_spec import Col, Fmt

if TYPE_CHECKING:
    from collections.abc import Mapping

    from models import Quote

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
