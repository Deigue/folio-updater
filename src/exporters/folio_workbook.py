"""The entire portfolio exported into an excel workbook."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING

from app import get_config
from db import get_connection, get_distinct_values, get_rows
from domain import Column, Table
from engine.panels import FOLIO_VIEW, account_view, type_view
from exporters.sheets import (
    cost_base_table,
    flows_table,
    fx_table,
    ledger_table,
    panel_table,
    summary_table,
    tickers_table,
    transactions_table,
)
from exporters.table import Link
from services import ForexService
from services.quotes_service import QuotesService
from services.symbols import load_symbol_resolver

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    import pandas as pd

    from engine.panels import FolioValuation, Panel
    from exporters.table import Table as Sheet
    from services.quotes_service import Quote


class Section(StrEnum):
    """A group of sheets that `--only` can ask for by name."""

    SUMMARY = "summary"  # the headline figures and every pool's flows
    DASH = "dash"  # the portfolio's dashboard, and one per account type
    ACCOUNTS = "accounts"  # one dashboard per broker account
    ACB = "acb"  # the Ledger, and every pool's closing cost base
    STORED = "stored"  # FX rates and tickers, and the importable Txns


SECTIONS: tuple[Section, ...] = tuple(Section)

# The sections that need a replay; without one only the stored tables remain.
VALUED: frozenset[Section] = frozenset(
    {Section.SUMMARY, Section.DASH, Section.ACCOUNTS, Section.ACB},
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
        The sections, in the order the workbook lays them out.

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
    """Collection of folio data-sources for the workbook generation.

    Attributes:
        valuation: The replay, priced, with every pool's rollups on hand. None
            when the folio could not be replayed, which leaves only the stored
            tables to write.
        txns: The stored transactions, as the database holds them.
        rates: The stored USDCAD rates.
        tickers: Each traded ticker, paired with the symbol it resolves to.
        quotes: The whole quote cache, keyed by symbol, for the tickers sheet.
    """

    valuation: FolioValuation | None
    txns: pd.DataFrame
    rates: pd.DataFrame
    tickers: list[tuple[str, str]]
    quotes: Mapping[str, Quote]

    @classmethod
    def read(cls, valuation: FolioValuation | None) -> FolioSources:
        """Read the stored tables a workbook reproduces.

        Args:
            valuation: The priced replay the other sheets are laid out from, or
                None when there is none.

        Returns:
            The sources, ready to lay out.
        """
        ticker = str(Column.Txn.TICKER)
        with get_connection() as conn:
            txns = get_rows(
                conn,
                Table.TXNS,
                order_by=f'"{Column.Txn.TXN_ID}"',
            )
            traded = get_distinct_values(
                conn,
                Table.TXNS,
                ticker,
                filter_condition=f'"{ticker}" IS NOT NULL AND "{ticker}" != \'\'',
                order_by=f'"{ticker}"',
            )

        resolver = load_symbol_resolver()
        names = [str(name) for name in traded.get(ticker, [])]
        return cls(
            valuation=valuation,
            txns=txns,
            rates=ForexService.get_fx_rates_from_db(),
            tickers=[(name, resolver.canonical(name)) for name in names],
            quotes=QuotesService.cached(),
        )


def folio_tables(
    sources: FolioSources,
    sections: Iterable[Section] = SECTIONS,
    *,
    notes: Sequence[str] = (),
) -> list[Sheet]:
    """Generate the whole folio, one table per sheet.

    Args:
        sources: What the workbook is read from.
        sections: The groups of sheets to include.
        notes: Lines every priced sheet discloses, such as how old the prices
            are.

    Returns:
        The tables, in the order their sheets should appear.
    """
    chosen = set(sections)
    pools = _pools(sources.valuation) if chosen & VALUED else None

    dashboards: list[Sheet] = []
    cost_base: list[Sheet] = []
    if pools is not None:
        dashboards = _dashboards(pools, chosen, notes)
        if Section.ACB in chosen and sources.valuation is not None:
            frame = sources.valuation.frame
            cost_base = [
                ledger_table(frame, txns=sources.txns),
                cost_base_table(frame),
            ]
    stored = _stored(sources) if Section.STORED in chosen else []

    if pools is None or Section.SUMMARY not in chosen:
        return [*dashboards, *cost_base, *stored]

    folio, *_ = pools.every
    flows = flows_table(pools.every)
    linked = [flows, *cost_base, *(table for table in stored if not table.hidden)]
    summary = summary_table(
        folio,
        pools.every,
        notes=notes,
        contents=[(table.name, _describe(table.name)) for table in linked],
    )
    reports = [_back_to_summary(table) for table in (flows, *dashboards, *cost_base)]
    return [summary, *reports, *stored]


_SUMMARY = "Summary"

# What the Summary's contents panel says each linked sheet holds.
_DESCRIPTIONS: dict[str, str] = {
    "Flows": "Cash, contributions and room, for every pool",
    "Ledger": "Every transaction, with its cost base at every grain",
    "Cost Base": "Every pool's closing cost base, in CAD",
}


@dataclass(frozen=True)
class _Pools:
    """Every pool the workbook reports, valued once.

    Attributes:
        folio: The whole portfolio.
        types: Each account type, active ones first.
        accounts: Each account, active ones first.
    """

    folio: Panel
    types: list[Panel]
    accounts: list[Panel]

    @property
    def every(self) -> list[Panel]:
        """The portfolio, then every type, then every account."""
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
    )


def _dashboards(
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
    return [panel_table(panel, notes=notes) for panel in panels]


def _stored(sources: FolioSources) -> list[Sheet]:
    """Return list of stored tables: rates, tickers, and the importable Txns.

    The Ledger shows a reader every stored column, so `Txns` is hidden: it is
    there for `folio import`, which finds it by name either way.
    """
    config = get_config()
    return [
        transactions_table(sources.txns, config.txn_sheet, hidden=True),
        fx_table(sources.rates, config.fx_sheet),
        tickers_table(sources.tickers, sources.quotes, config.tkr_sheet),
    ]


def _describe(sheet: str) -> str:
    """Say what a linked sheet holds, for the Summary's contents panel."""
    config = get_config()
    stored = {
        config.fx_sheet: "The Bank of Canada USDCAD rates behind every conversion",
        config.tkr_sheet: "Every ticker traded, with its latest quote",
    }
    return _DESCRIPTIONS.get(sheet) or stored.get(sheet, "")


def _back_to_summary(table: Sheet) -> Sheet:
    """Give a report sheet a way back to the sheet the workbook opens on."""
    return replace(table, notes=(Link("Back to Summary", _SUMMARY), *table.notes))


def _active_first(panels: list[Panel]) -> list[Panel]:
    """Put the pools still holding securities ahead of the fully closed ones."""
    return sorted(panels, key=lambda panel: not panel.active)
