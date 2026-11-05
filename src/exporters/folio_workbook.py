"""The folio workbook: the whole folio rendered into an Excel workbook."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from app import get_config
from db import get_connection, get_distinct_values, get_rows
from domain import Column
from domain import Table as DbTable
from engine.gain_since_buy import with_gains_since_buy
from engine.panels import FOLIO_VIEW, account_view, type_view
from engine.ticker import MARKET_SORT, performance_rows, sort_performance
from exporters.sheet_spec import Link
from exporters.sheets import (
    COST_BASE_SHEET,
    FLOWS_SHEET,
    LEDGER_SHEET,
    PERFORMANCE_SHEET,
    SUMMARY_SHEET,
    cost_base_sheet,
    flows_sheet,
    fx_sheet,
    ledger_sheet,
    panel_sheet,
    performance_sheet,
    summary_sheet,
    traded_tickers_sheet,
    transactions_sheet,
)
from services import FxService
from services.quotes_service import QuotesService
from services.symbols import load_symbol_resolver

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    import pandas as pd

    from domain import PriceRange
    from engine.flows import Room
    from engine.panels import FolioValuation, Panel
    from engine.performance import RangeMove
    from exporters.sheet_spec import Sheet
    from services.quotes_service import Quote


class Section(StrEnum):
    """A group of sheets that `--only` can ask for by name."""

    SUMMARY = "summary"  # the headline figures and every pool's flows
    PERF = "perf"  # every holding's price move over each range
    DASH = "dash"  # the folio's dashboard, and one per account type
    ACCOUNTS = "accounts"  # one dashboard per broker account
    ACB = "acb"  # the Ledger, and every pool's closing cost base
    STORED = "stored"  # FX rates and tickers, and the importable Txns


SECTIONS: tuple[Section, ...] = tuple(Section)

# The sections that need a replay; without one only the stored tables remain.
VALUED: frozenset[Section] = frozenset(
    {Section.SUMMARY, Section.PERF, Section.DASH, Section.ACCOUNTS, Section.ACB},
)


class UnknownSectionError(ValueError):
    """An `--only` naming a group of sheets that does not exist."""

    def __init__(self, name: str) -> None:
        """Name the bad section and every one that would have worked."""
        self.name = name
        known = ", ".join(str(section) for section in SECTIONS)
        super().__init__(f"There is no section called '{name}'. Try: {known}.")


def parse_sections(value: str | None) -> tuple[Section, ...]:
    """Read an `--only` argument into the sections it names.

    Args:
        value: Section names separated by commas, or None for every section.

    Returns:
        The sections, in the order the workbook lists them.

    Raises:
        UnknownSectionError: If a name is not a section.
    """
    if value is None:
        return SECTIONS
    wanted: set[Section] = set()
    for token in value.split(","):
        name = token.strip().lower()
        if not name:
            continue
        try:
            wanted.add(Section(name))
        except ValueError:
            raise UnknownSectionError(name) from None
    return tuple(section for section in SECTIONS if section in wanted)


@dataclass(frozen=True)
class FolioSources:
    """What a workbook is read from: the priced replay and the stored tables.

    Attributes:
        valuation: The replay, priced, with every pool's rollups on hand. None
            when the folio could not be replayed, which leaves only the stored
            tables to write.
        txns: The stored transactions, as the database holds them.
        rates: The stored USDCAD rates.
        tickers: Each traded ticker, paired with the symbol it resolves to.
        quotes: The whole quote cache, keyed by symbol, for the tickers sheet.
        moves: Each held symbol's price move over every range, for the
            Performance sheet. Empty when that sheet was not asked for.
    """

    valuation: FolioValuation | None
    txns: pd.DataFrame
    rates: pd.DataFrame
    tickers: list[tuple[str, str]]
    quotes: Mapping[str, Quote]
    moves: Mapping[str, Mapping[PriceRange, RangeMove | None]] = field(
        default_factory=dict,
    )

    @classmethod
    def read(
        cls,
        valuation: FolioValuation | None,
        moves: Mapping[str, Mapping[PriceRange, RangeMove | None]] | None = None,
    ) -> FolioSources:
        """Read the stored tables a workbook reproduces.

        Args:
            valuation: The priced replay the other sheets are printed from, or
                None when there is none.
            moves: Each held symbol's move over every range, when the
                Performance sheet is wanted.

        Returns:
            The sources, ready to print.
        """
        ticker = str(Column.Txn.TICKER)
        with get_connection() as conn:
            txns = get_rows(
                conn,
                DbTable.TXNS,
                order_by=f'"{Column.Txn.TXN_ID}"',
            )
            traded = get_distinct_values(
                conn,
                DbTable.TXNS,
                ticker,
                filter_condition=f'"{ticker}" IS NOT NULL AND "{ticker}" != \'\'',
                order_by=f'"{ticker}"',
            )

        resolver = load_symbol_resolver()
        names = [str(name) for name in traded.get(ticker, [])]
        return cls(
            valuation=valuation,
            txns=txns,
            rates=FxService.get_fx_rates_from_db(),
            tickers=[(name, resolver.canonical(name)) for name in names],
            quotes=QuotesService.cached(),
            moves=moves or {},
        )


def folio_sheets(
    sources: FolioSources,
    sections: Iterable[Section] = SECTIONS,
    *,
    notes: Sequence[str] = (),
) -> list[Sheet]:
    """Generate the whole folio, sheet by sheet.

    Args:
        sources: What the workbook is read from.
        sections: The groups of sheets to include.
        notes: Lines every priced sheet discloses, such as how old the prices
            are.

    Returns:
        The sheets, in the order they should appear.
    """
    chosen = set(sections)
    pools = _pools(sources.valuation) if chosen & VALUED else None

    dashboards: list[Sheet] = []
    cost_base: list[Sheet] = []
    if pools is not None:
        dashboards = _dashboard_sheets(pools, chosen, notes)
        if Section.ACB in chosen and sources.valuation is not None:
            frame = sources.valuation.frame
            valued = with_gains_since_buy(
                frame,
                sources.quotes,
                sources.valuation.folio_holdings.fx,
            )
            cost_base = [
                ledger_sheet(valued, txns=sources.txns),
                cost_base_sheet(frame),
            ]
    performance: list[Sheet] = []
    if pools is not None and Section.PERF in chosen:
        performance = [_performance_sheet(pools.folio, sources, notes)]
    stored = _stored_sheets(sources) if Section.STORED in chosen else []

    if pools is None or Section.SUMMARY not in chosen:
        return [*performance, *dashboards, *cost_base, *stored]

    flows = flows_sheet(pools.every, history=pools.history)
    linked = [
        flows,
        *performance,
        *cost_base,
        *(sheet for sheet in stored if not sheet.hidden),
    ]
    summary = summary_sheet(
        pools.folio,
        pools.every,
        notes=notes,
        contents=[(sheet.name, _describe(sheet.name)) for sheet in linked],
    )
    reports = [
        _back_to_summary(sheet)
        for sheet in (flows, *performance, *dashboards, *cost_base)
    ]
    linked_stored = [
        sheet if sheet.hidden else _back_to_summary(sheet) for sheet in stored
    ]
    return [summary, *reports, *linked_stored]


# What the Summary's contents list says each linked sheet holds.
_DESCRIPTIONS: dict[str, str] = {
    FLOWS_SHEET: "Cash, contributions and room, for every pool",
    PERFORMANCE_SHEET: "Every holding's price move over each range, 2h to all time",
    LEDGER_SHEET: "Every transaction, with its cost base at every grain",
    COST_BASE_SHEET: "Every pool's closing cost base, in CAD",
}


@dataclass(frozen=True)
class _Pools:
    """Every pool the workbook reports, valued once.

    Attributes:
        folio: The whole folio.
        types: Each account type, active ones first.
        accounts: Each account, active ones first.
        history: Every year of contribution room used, newest first.
    """

    folio: Panel
    types: list[Panel]
    accounts: list[Panel]
    history: list[Room]

    @property
    def every(self) -> list[Panel]:
        """The folio, then every type, then every account."""
        return [self.folio, *self.types, *self.accounts]


def _pools(valuation: FolioValuation | None) -> _Pools | None:
    """Value every pool once, or None when the folio could not be replayed."""
    if valuation is None:
        return None
    return _Pools(
        folio=valuation.panel(FOLIO_VIEW),
        types=_active_first(
            valuation.panels([type_view(name) for name in valuation.account_types()]),
        ),
        accounts=_active_first(
            valuation.panels([account_view(name) for name in valuation.accounts()]),
        ),
        history=valuation.room_history,
    )


def _dashboard_sheets(
    pools: _Pools,
    chosen: set[Section],
    notes: Sequence[str],
) -> list[Sheet]:
    """Dashboard for each pool the sections ask for."""
    panels: list[Panel] = []
    if Section.DASH in chosen:
        panels.extend((pools.folio, *pools.types))
    if Section.ACCOUNTS in chosen:
        panels.extend(pools.accounts)
    return [panel_sheet(panel, notes=notes) for panel in panels]


def _performance_sheet(
    folio: Panel,
    sources: FolioSources,
    notes: Sequence[str],
) -> Sheet:
    """Compare every holding over each range, largest market value first."""
    quotes = {
        holding.symbol: holding_quote
        for holding in folio.holdings.holdings
        if (holding_quote := sources.quotes.get(holding.symbol)) is not None
    }
    rows = performance_rows(folio, quotes, sources.moves)
    return performance_sheet(sort_performance(rows, MARKET_SORT), notes=notes)


def _stored_sheets(sources: FolioSources) -> list[Sheet]:
    """List the stored tables: rates, tickers, and the importable Txns.

    The Ledger shows a reader every stored column, so `Txns` is hidden: it is
    there for `folio import`, which finds it by name either way.
    """
    config = get_config()
    return [
        transactions_sheet(sources.txns, config.txn_sheet, hidden=True),
        fx_sheet(sources.rates, config.fx_sheet),
        traded_tickers_sheet(sources.tickers, sources.quotes, config.tickers_sheet),
    ]


def _describe(sheet: str) -> str:
    """Say what a linked sheet holds, for the Summary's contents list."""
    config = get_config()
    stored = {
        config.fx_sheet: "The Bank of Canada USDCAD rates behind every conversion",
        config.tickers_sheet: "Every ticker traded, with its latest quote",
    }
    return _DESCRIPTIONS.get(sheet) or stored.get(sheet, "")


def _back_to_summary(sheet: Sheet) -> Sheet:
    """Give a report sheet a way back to the sheet the workbook opens on."""
    return replace(sheet, nav=Link("Back to Summary", SUMMARY_SHEET))


def _active_first(panels: list[Panel]) -> list[Panel]:
    """Put the pools still holding securities ahead of the fully closed ones."""
    return sorted(panels, key=lambda panel: not panel.active)
