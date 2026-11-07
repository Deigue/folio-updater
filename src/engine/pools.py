"""Valuation of every pool's holdings and flows, from one replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, NamedTuple

from app import get_config
from domain import AccountType, Column, Currency, Grain
from domain.constants import TORONTO_TZ
from engine.accounts import resolve_account_type
from engine.flows import (
    build_flows,
    contributions_by_type_year,
    room_history,
    withdrawals_by_type_year,
)
from engine.fx_rates import load_fx_rates
from engine.holdings import FolioHoldings
from services.quotes_service import QuotesService
from services.symbols import load_symbol_resolver

if TYPE_CHECKING:
    from collections.abc import Mapping
    from decimal import Decimal

    import pandas as pd

    from engine.flows import Flows, Room
    from engine.holdings import DisplayCurrency, HoldingSet
    from engine.ledger import Ledger
    from services.quotes_service import Quote

# What reports call the folio pool.
FOLIO_LABEL = "Portfolio"


class Pool(NamedTuple):
    """Which pool: an account, an account type, or the whole folio.

    Attributes:
        grain: The grain the pool sits at.
        name: The account name or account type its rows are filtered to. Empty
            at folio grain, where nothing is filtered out.
    """

    grain: Grain
    name: str

    @property
    def label(self) -> str:
        """How the pool reads in a heading.

        Returns:
            The account name, the account type with `-` for `_`
            (`NON-REGISTERED`), or `Portfolio` for the whole folio.
        """
        if self.grain is Grain.TYPE:
            return self.name.replace("_", "-")
        if self.grain is Grain.ACCOUNT:
            return self.name
        return FOLIO_LABEL


FOLIO = Pool(Grain.FOLIO, "")


@dataclass(frozen=True)
class ValuedPool:
    """One pool, valued: what it holds and what moved through it.

    Attributes:
        pool: The pool this reports on.
        holdings: Its valued holdings and totals.
        flows: Its cash, contributions and cumulative income.
    """

    pool: Pool
    holdings: HoldingSet
    flows: Flows

    @property
    def occupied(self) -> bool:
        """Whether the pool holds anything, or ever did."""
        return bool(self.holdings.holdings or self.holdings.closed)

    @property
    def active(self) -> bool:
        """Whether the pool still has an open holding."""
        return bool(self.holdings.holdings)

    @property
    def open_symbols(self) -> list[str]:
        """Symbols of the open holdings, in the order the pool lists them."""
        return [holding.symbol for holding in self.holdings.holdings]


def account_type_of(pool: Pool) -> AccountType | None:
    """Name the account type a pool is, when it is exactly one.

    Contribution room only means something for a single account type, so the
    folio pool has none.

    Args:
        pool: The pool being reported.

    Returns:
        The account type, or None when the pool spans more than one.
    """
    if pool.grain is Grain.TYPE:
        try:
            return AccountType(pool.name)
        except ValueError:  # callers resolve the type first
            return None
    if pool.grain is Grain.ACCOUNT:
        return resolve_account_type(pool.name)
    return None


def type_pool(name: str) -> Pool:
    """Name one account type's pool."""
    return Pool(Grain.TYPE, name)


def account_pool(name: str) -> Pool:
    """Name one broker account's pool."""
    return Pool(Grain.ACCOUNT, name)


@dataclass(frozen=True)
class FolioValuation:
    """Everything a report needs about one replay, priced once.

    Attributes:
        folio_holdings: The frame and quotes, with each pool's rollups memoized.
        ledger: The ledger the holdings and cash totals are read from.
        currency: The currency figures are expressed in, or `native`.
        contributions: Every type's per-year contributions, for the room line.
        withdrawals: Every type's per-year withdrawals, for the room history.
        quotes: The quote used for each held symbol.
        folio_market: The whole folio's CAD market value, which is the
            denominator behind `Folio%` however narrow a pool is.
    """

    folio_holdings: FolioHoldings
    ledger: Ledger
    currency: DisplayCurrency
    contributions: Mapping[AccountType, Mapping[int, Decimal]]
    withdrawals: Mapping[AccountType, Mapping[int, Decimal]]
    quotes: dict[str, Quote]
    folio_market: Decimal | None = None

    @classmethod
    def build(
        cls,
        ledger: Ledger,
        *,
        currency: DisplayCurrency = Currency.CAD,
        refresh: bool = False,
        offline: bool = False,
    ) -> FolioValuation:
        """Price the whole folio once, ready to report any pool of it.

        Args:
            ledger: The ledger to value.
            currency: The currency to express figures in, or `native`.
            refresh: Refetch every quote regardless of its age.
            offline: Never touch the network; use whatever is cached.

        Returns:
            The valuation, with every open holding priced.
        """
        frame = ledger.frame
        folio_holdings = FolioHoldings(frame, {}, load_fx_rates())
        quotes = _quotes(folio_holdings, refresh=refresh, offline=offline)
        folio_holdings.price(quotes)
        return cls(
            folio_holdings=folio_holdings,
            ledger=ledger,
            currency=currency,
            contributions=contributions_by_type_year(frame),
            withdrawals=withdrawals_by_type_year(frame),
            quotes=quotes,
            folio_market=folio_holdings.market_value(),
        )

    @property
    def frame(self) -> pd.DataFrame:
        """The replayed ledger every pool is valued from."""
        return self.folio_holdings.frame

    @property
    def room_history(self) -> list[Room]:
        """Every year of contribution room the folio has used, newest first."""
        return room_history(
            self.contributions,
            self.withdrawals,
            get_config().contribution_room,
            through=datetime.now(TORONTO_TZ).year,
        )

    def value(
        self,
        pool: Pool,
        *,
        sort: str | None = None,
        reverse: bool = False,
    ) -> ValuedPool:
        """Value one pool: its holdings and its flows together.

        From the replay already priced once, takes the pool's rows at its grain
        and rolls them up into holdings (units, cost, market value at the cached
        quotes, in the valuation's currency, weights against the whole folio),
        then adds the pool's flows (cash, contributions, withdrawals, dividends,
        fees, realized gains, and contribution room when the pool is one account
        type).

        Args:
            pool: The pool to value.
            sort: Order the holdings by this measure instead of market value.
            reverse: Flip the sort's natural direction.

        Returns:
            The pool, valued: a view or sheet only has to lay it out, with
            nothing left to compute.

        Raises:
            UnknownSortError: If `sort` is not a sortable measure.
        """
        holdings = self.folio_holdings.holdings(
            grain=pool.grain,
            pool=pool.name,
            currency=self.currency,
            folio_market=self.folio_market,
            sort=sort,
            reverse=reverse,
        )
        flows = build_flows(
            self.ledger,
            grain=pool.grain,
            pool=pool.name,
            label=pool.label,
            account_type=account_type_of(pool),
            room=get_config().contribution_room,
            contributions=self.contributions,
        )
        return ValuedPool(pool=pool, holdings=holdings, flows=flows)

    def account_types(self) -> list[str]:
        """Every account type with a holding, in a stable order."""
        return self._pools(Column.Ledger.ACCT_TYPE)

    def accounts(self) -> list[str]:
        """Every account with a holding, in a stable order."""
        return self._pools(Column.Txn.ACCOUNT)

    def value_all(
        self,
        pools: list[Pool],
        *,
        sort: str | None = None,
        reverse: bool = False,
    ) -> list[ValuedPool]:
        """Value several pools at one grain, as `value` does each one.

        The priced replay is split by pool once, rather than narrowed again for
        each pool.

        Args:
            pools: The pools to value, all at the same grain.
            sort: Order each pool's holdings by this measure.
            reverse: Flip the sort's natural direction.

        Returns:
            One valued pool per pool that holds something, or used to, in the
            order given. Pools that never held anything are left out.
        """
        if pools:
            self.folio_holdings.prime(pools[0].grain)
        valued = (self.value(pool, sort=sort, reverse=reverse) for pool in pools)
        return [each for each in valued if each.occupied]

    def _pools(self, column: str) -> list[str]:
        """Name every pool a column holds, in a stable order."""
        return sorted({str(value) for value in self.frame[column].dropna()})


def _quotes(
    folio_holdings: FolioHoldings,
    *,
    refresh: bool,
    offline: bool,
) -> dict[str, Quote]:
    """Price every open holding, once, for the whole folio."""
    symbols = folio_holdings.held_symbols()
    if not symbols:
        return {}
    return QuotesService.get_quotes(
        symbols,
        load_symbol_resolver(),
        refresh=refresh,
        offline=offline,
    )
