"""Valuation of every pool's holdings and flows, from one replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from app import get_config
from domain import AccountType, Column, Currency, Scope
from engine.accounts import resolve_account_type
from engine.flows import build_flows, contributions_by_type_year
from engine.fx_rates import load_fx_rates
from engine.positions import FolioPositions, scope_rows
from services.quotes_service import QuotesService
from services.symbols import load_symbol_resolver

if TYPE_CHECKING:
    from collections.abc import Mapping
    from decimal import Decimal

    import pandas as pd

    from engine.flows import Flows
    from engine.positions import HoldingSet, ValuationCurrency
    from engine.types import ReplayResult
    from services.quotes_service import Quote


class PoolView(NamedTuple):
    """One resolved request for a pooled report.

    Attributes:
        scope: The pool grain to report at.
        pool: The account name or account type the rows are filtered to. Empty
            at portfolio grain, where nothing is filtered out.
        label: How the scope reads in a heading.
    """

    scope: Scope
    pool: str
    label: str


FOLIO_VIEW = PoolView(Scope.FOLIO, "", "Portfolio")


@dataclass(frozen=True)
class Panel:
    """One pool, valued: what it holds and what moved through it.

    Attributes:
        view: The pool this reports on.
        holdings: Its valued positions and totals.
        flows: Its cash, contributions and cumulative income.
    """

    view: PoolView
    holdings: HoldingSet
    flows: Flows

    @property
    def occupied(self) -> bool:
        """Whether the pool holds anything, or ever did."""
        return bool(self.holdings.holdings or self.holdings.closed)


def account_type_of(view: PoolView) -> AccountType | None:
    """Name the account type a pool is, when it is exactly one.

    Contribution room only means something for a single tax type, so a
    portfolio-wide view has none.

    Args:
        view: The pool being reported.

    Returns:
        The account type, or None when the pool spans more than one.
    """
    if view.scope is Scope.TYPE:
        try:
            return AccountType(view.pool)
        except ValueError:  # pragma: no cover - callers resolve the type first
            return None
    if view.scope is Scope.ACCOUNT:
        return resolve_account_type(view.pool)
    return None


def type_view(name: str) -> PoolView:
    """Name one account type's pool, the way a heading reads it."""
    return PoolView(Scope.TYPE, name, name.replace("_", "-"))


def account_view(name: str) -> PoolView:
    """Name one broker account's pool."""
    return PoolView(Scope.ACCOUNT, name, name)


@dataclass(frozen=True)
class FolioValuation:
    """Everything a report needs about one replay, priced once.

    Attributes:
        positions: The frame and quotes, with each pool's rollups memoized.
        result: The replay the cash totals are read from.
        currency: The currency figures are expressed in, or `native`.
        contributions: Every type's per-year contributions, for the room line.
        quotes: The quote used for each held symbol.
        folio_market: The whole portfolio's CAD market value, which is the
            denominator behind `Folio%` however narrow a panel is.
    """

    positions: FolioPositions
    result: ReplayResult
    currency: ValuationCurrency
    contributions: Mapping[AccountType, Mapping[int, Decimal]]
    quotes: dict[str, Quote]
    folio_market: Decimal | None = None

    @classmethod
    def build(
        cls,
        frame: pd.DataFrame,
        result: ReplayResult,
        *,
        currency: ValuationCurrency = Currency.CAD,
        refresh: bool = False,
        offline: bool = False,
    ) -> FolioValuation:
        """Price the whole folio once, ready to report any pool of it.

        Args:
            frame: The replayed master frame.
            result: The replay behind it, which carries the cash totals.
            currency: The currency to express figures in, or `native`.
            refresh: Refetch every quote regardless of its age.
            offline: Never touch the network; use whatever is cached.

        Returns:
            The valuation, with every open position priced.
        """
        positions = FolioPositions(frame, {}, load_fx_rates())
        quotes = _quotes(positions, refresh=refresh, offline=offline)
        positions.price(quotes)
        return cls(
            positions=positions,
            result=result,
            currency=currency,
            contributions=contributions_by_type_year(frame),
            quotes=quotes,
            folio_market=positions.market_value(),
        )

    @property
    def frame(self) -> pd.DataFrame:
        """The replayed master frame every panel is read from."""
        return self.positions.frame

    def panel(
        self,
        view: PoolView,
        *,
        sort: str | None = None,
        reverse: bool = False,
    ) -> Panel:
        """Build one pool's holdings and flows together.

        Args:
            view: The pool to report on.
            sort: Order the holdings by this measure instead of market value.
            reverse: Flip the sort's natural direction.

        Returns:
            The pool, valued.

        Raises:
            UnknownSortError: If `sort` is not a sortable measure.
        """
        holdings = self.positions.holdings(
            scope=view.scope,
            pool=view.pool,
            currency=self.currency,
            folio_market=self.folio_market,
            sort=sort,
            reverse=reverse,
        )
        flows = build_flows(
            self.result,
            scope=view.scope,
            pool=view.pool,
            label=view.label,
            account_type=account_type_of(view),
            room=get_config().contribution_room,
            contributions=self.contributions,
        )
        return Panel(view=view, holdings=holdings, flows=flows)

    def account_types(self) -> list[str]:
        """Every account type that holds a position, in a stable order."""
        return self._pools(Scope.TYPE, "AcctType")

    def accounts(self) -> list[str]:
        """Every account that holds a position, in a stable order."""
        return self._pools(Scope.ACCOUNT, str(Column.Txn.ACCOUNT))

    def panels(
        self,
        views: list[PoolView],
        *,
        sort: str | None = None,
        reverse: bool = False,
    ) -> list[Panel]:
        """Value several pools at one grain, skipping the empty ones.

        Args:
            views: The pools to report on, all at the same grain.
            sort: Order each pool's holdings by this measure.
            reverse: Flip the sort's natural direction.

        Returns:
            One panel per pool that holds something, or used to.
        """
        if views:
            # Every pool at this grain is about to be reported, so split the
            # frame once rather than narrowing it again for each panel.
            self.positions.prime(views[0].scope)
        built = (self.panel(view, sort=sort, reverse=reverse) for view in views)
        return [panel for panel in built if panel.occupied]

    def _pools(self, scope: Scope, column: str) -> list[str]:
        """Name every pool at one grain that has rows of its own."""
        frame = self.frame
        if column not in frame.columns:  # pragma: no cover - always present
            return []
        return [
            name
            for name in sorted({str(value) for value in frame[column].dropna()})
            if not scope_rows(frame, scope, name).empty
        ]


def _quotes(
    positions: FolioPositions,
    *,
    refresh: bool,
    offline: bool,
) -> dict[str, Quote]:
    """Price every open position, once, for the whole folio."""
    symbols = positions.held_symbols()
    if not symbols:
        return {}
    return QuotesService.get_quotes(
        symbols,
        load_symbol_resolver(),
        refresh=refresh,
        offline=offline,
    )
