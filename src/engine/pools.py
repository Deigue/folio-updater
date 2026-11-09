"""Valuation of every pool's holdings and flows, from one replay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from app import get_config
from domain import (
    AccountType,
    Column,
    Currency,
    DiagnosticCode,
    Grain,
)
from domain.constants import TORONTO_TZ
from engine.accounts import Pool, account_type_of
from engine.flows import (
    build_flows,
    contributions_by_type_year,
    room_history,
    withdrawals_by_type_year,
)
from engine.fx_rates import load_fx_rates
from engine.holdings import UNITS_SUSPECT, FolioHoldings
from services.quotes_service import QuotesService
from services.symbols import load_symbol_resolver

if TYPE_CHECKING:
    from collections.abc import Mapping
    from decimal import Decimal

    from engine.flows import Flows, Room
    from engine.holdings import CurrencyTotals, DisplayCurrency, HoldingSet
    from engine.ledger import Ledger
    from models import Quote


@dataclass(frozen=True)
class Return:
    """What one totals row's `Total%` states.

    Attributes:
        ratio: The return, or None to leave the cell blank.
        gain: The gain the ratio was measured on, in the currency of that
            measurement.
    """

    ratio: Decimal | None = None
    gain: Decimal | None = None


class Conversion(StrEnum):
    """Which figures a conversion at the current rate produced."""

    TOTAL = "total"  # The grand total, summed across currencies
    TOTAL_RETURN = "total_return"  # Only `Total%`, a CAD ratio beside native figures
    VALUED = "valued"  # Every figure, the whole pool shown in CAD


@dataclass(frozen=True)
class Converted:
    """Some figures were converted at the current USDCAD rate.

    Attributes:
        what: Which figures the conversion produced.
        into: The currency they were converted into.
        rate: The USDCAD rate used.
        rate_date: The date that rate is from.
    """

    what: Conversion
    into: Currency
    rate: Decimal
    rate_date: str | None


@dataclass(frozen=True)
class Unpriced:
    """Holdings with no price, left out of every total.

    Attributes:
        symbols: The holdings left out.
    """

    symbols: tuple[str, ...]


@dataclass(frozen=True)
class TotalReturnHidden:
    """`Total%` is blank because the currency filter hid part of the pool.

    It measures the whole pool against its net deposits, so no ratio describes
    the part left on show.

    Attributes:
        excluded: How many holdings the filter hid.
        shown: The currency left on show.
    """

    excluded: int
    shown: Currency


@dataclass(frozen=True)
class UnitsSuspect:
    """Some holdings carry diagnostics that mean their units are wrong.

    Attributes:
        codes: The diagnostics found, in the order the holdings carry them.
    """

    codes: tuple[DiagnosticCode, ...]


# A statement of what a pool's figures leave out or were converted at.
Disclosure = Unpriced | TotalReturnHidden | Converted | UnitsSuspect


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
    def label(self) -> str:
        """How the pool reads in a heading or a sheet name."""
        return self.pool.label

    @property
    def grain(self) -> Grain:
        """The grain the pool was valued at."""
        return self.pool.grain

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

    @property
    def total_return(self) -> Return:
        """The pool's total gain as a share of its net deposits.

        Against net deposits rather than book value: see `HoldingSet.return_on`.
        """
        return Return(self._deposit_return, self.holdings.total_pnl)

    @property
    def _deposit_return(self) -> Decimal | None:
        """The pool's CAD gain over its net CAD deposits, when both are known."""
        return self.holdings.return_on(self.flows.net_deposit_denominator)

    def subtotal_return(self, group: CurrencyTotals) -> Return:
        """Say what one currency group's subtotal reports as its `Total%`.

        A pool with one currency group has no grand total below it, so its
        subtotal is the pool's overall total and is promoted to the return on
        net deposits. A USD-only pool is still one pool with one deposit
        history, so it is promoted the same way, and both sides of the ratio
        stay in CAD however the pool is displayed.

        Args:
            group: One of the pool's currency groups.

        Returns:
            The group's own book-based return when a grand total follows, the
            promoted return when this is the only group, and a blank when the
            currency filter hides part of the pool from a whole-pool
            denominator.
        """
        holdings = self.holdings
        if holdings.mixed_currency:
            return Return(group.total_pnl_pct, group.total_pnl)
        if not holdings.deposits_measurable:
            return Return()
        promoted = self._deposit_return
        if promoted is None:
            return Return(group.total_pnl_pct, group.total_pnl)
        return Return(promoted, holdings.total_pnl_cad)

    @property
    def disclosures(self) -> tuple[Disclosure, ...]:
        """State what the pool's figures leave out or were converted at."""
        holdings = self.holdings
        found: list[Disclosure] = []
        if holdings.unpriced:
            found.append(Unpriced(holdings.unpriced))
        deposited = self.flows.net_deposit_denominator
        if not holdings.deposits_measurable and deposited is not None:
            found.append(TotalReturnHidden(holdings.excluded, holdings.totals_currency))
        converted = self._conversion()
        if converted is not None:
            found.append(converted)
        suspect = tuple(code for code in holdings.flags if code in UNITS_SUSPECT)
        if suspect:
            found.append(UnitsSuspect(suspect))
        return tuple(found)

    def _conversion(self) -> Converted | None:
        """Say what the current rate converted, when it converted anything.

        `-c USD` converts nothing, since the CAD holdings are dropped rather
        than converted. A single-currency pool that isn't CAD still has its
        `Total%` promoted to net deposits, a CAD figure, so that one cell is a
        conversion even though every other figure keeps its own currency.
        """
        holdings = self.holdings
        if holdings.fx_rate is None:
            return None
        if holdings.mixed_currency:
            what, into = Conversion.TOTAL, holdings.totals_currency
        elif holdings.display_currency == Currency.CAD:
            what, into = Conversion.VALUED, Currency.CAD
        elif self._promoted:
            what, into = Conversion.TOTAL_RETURN, Currency.CAD
        else:
            return None
        return Converted(what, into, holdings.fx_rate, holdings.fx_date)

    @property
    def _promoted(self) -> bool:
        """Whether a lone non-CAD group's `Total%` was promoted to a CAD ratio."""
        groups = self.holdings.by_currency
        if len(groups) != 1 or groups[0].currency is Currency.CAD:
            return False
        return self._deposit_return is not None


@dataclass(frozen=True)
class FolioValuation:
    """Everything a report needs about one replay, priced once.

    Attributes:
        folio_holdings: The holding states and quotes every pool is valued from.
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
        quotes = _quotes(ledger, refresh=refresh, offline=offline)
        folio_holdings = FolioHoldings(ledger.holdings, quotes, load_fx_rates())
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
            pool,
            currency=self.currency,
            folio_market=self.folio_market,
            sort=sort,
            reverse=reverse,
        )
        flows = build_flows(
            self.ledger,
            pool,
            account_type=account_type_of(pool),
            room=get_config().contribution_room,
            contributions=self.contributions,
        )
        return ValuedPool(pool=pool, holdings=holdings, flows=flows)

    def account_types(self) -> list[str]:
        """Every account type with a holding, in a stable order."""
        return self._pool_names(Column.Ledger.ACCT_TYPE)

    def accounts(self) -> list[str]:
        """Every account with a holding, in a stable order."""
        return self._pool_names(Column.Txn.ACCOUNT)

    def value_all(
        self,
        pools: list[Pool],
        *,
        sort: str | None = None,
        reverse: bool = False,
    ) -> list[ValuedPool]:
        """Value several pools at one grain, as `value` does each one.

        Args:
            pools: The pools to value, all at the same grain.
            sort: Order each pool's holdings by this measure.
            reverse: Flip the sort's natural direction.

        Returns:
            One valued pool per pool that holds something, or used to, in the
            order given. Pools that never held anything are left out.
        """
        valued_pools = (self.value(pool, sort=sort, reverse=reverse) for pool in pools)
        return [valued for valued in valued_pools if valued.occupied]

    def _pool_names(self, column: str) -> list[str]:
        """Name every pool a column holds, in a stable order."""
        return sorted({str(value) for value in self.ledger.frame[column].dropna()})


def _quotes(
    ledger: Ledger,
    *,
    refresh: bool,
    offline: bool,
) -> dict[str, Quote]:
    """Price every open holding, once, for the whole folio."""
    symbols = ledger.held_symbols
    if not symbols:
        return {}
    return QuotesService.get_quotes(
        symbols,
        load_symbol_resolver(),
        refresh=refresh,
        offline=offline,
    )
