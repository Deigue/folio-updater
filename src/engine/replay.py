"""The cost-base replay.

Walks the ledger and produces units, adjusted cost base, realized gains and cash
for all three pool grains at once: account, account type, and the whole
folio, via one pass.

**Two orderings, one call.** Units and cost base follow the *trade* date; cash
follows the *settlement* date. Ordering the cash replay by transaction date produces
phantom negative balances whenever a buy and the contribution funding it land on
the same day. Both walks share the row list and every accumulator, so all three grains
still come out of one pass over each ordering rather than three separate replays.

Six core rules followed:

1. `ROC` moves no cash, it reclassifies a distribution already paid.
2. Order the cash replay by settlement date.
3. Auto-detect the fee convention per account.
4. Two `FXT` shapes exist, distinguishable from the row itself.
5. Carry the cost base across a `TFR_IN`/`TFR_OUT` pair, never reading `Amount`.
"""

from __future__ import annotations

import logging
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from domain import (
    TAXABLE_ACCOUNT_TYPES,
    WEEKDAYS,
    AccountType,
    Action,
    Currency,
    DiagnosticCode,
    Grain,
)
from domain.numeric import ONE, ZERO, q2, safe_div
from engine.accounts import Account, Pool, resolve_account
from engine.deposits import NetDeposits
from engine.transfers import DUST_UNITS, TRANSFER_ACTIONS, Crossing, pair_transfers
from engine.types import (
    CashEffect,
    CashState,
    Diagnostic,
    GrainMeasures,
    HoldingState,
    LedgerRow,
    ReplayResult,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from engine.fees import FeeRules, TradeCash
    from engine.fx_rates import FxRates
    from engine.transfers import TransferPair
    from models import Txn
    from services.symbols import SymbolResolver

logger = logging.getLogger(__name__)

# A single-leg FXT's Amount, Units and Price are three views of one conversion,
# so they must agree. Half a cent tolerance to capture subcent rounding.
FXT_TOLERANCE = Decimal("0.005")

# A realized loss followed by a buy of the same symbol inside this window is a
# superficial-loss candidate. Just a flag.
SUPERFICIAL_LOSS_DAYS = 30

# Where a sale's loss is checked for being superficial: the taxable accounts,
# where CRA would deny it, and unknown accounts, which get every diagnostic
# any account could. An unknown account still counts in no tax figure.
SUPERFICIAL_LOSS_TYPES = TAXABLE_ACCOUNT_TYPES | {AccountType.UNKNOWN}

# Order actions share a transaction date in. The date is the finest resolution the folio
# records, so several rows routinely land on one and their true sequence is not
# recoverable. This prioritization makes things deterministic and avoid cost base
# problems.
#
#   - acquisitions first, so a same-day transfer or sale has something to move
#   - a transfer's out leg before its in leg, so the cost base being carried is
#     known by the time the in leg claims it
#   - sales last, against the largest holding, so a same-day buy-then-sell does
#     not read as an oversell.
#
# Actions that dont impact cost base sort last, where their order is irrelevant.
_INTRADAY_ORDER: dict[Action, int] = {
    Action.BUY: 0,
    Action.TFR_OUT: 1,
    Action.TFR_IN: 2,
    Action.SPLIT: 3,
    Action.ROC: 4,
    Action.SELL: 5,
}
_INTRADAY_DEFAULT = 6


def intraday_rank(action: object) -> int:
    """Rank an action within its day, in the order the replay applies it.

    Args:
        action: The row's action, as the enum or as its stored text.

    Returns:
        Its rank; lower is applied first on the same transaction date.
    """
    try:
        return _INTRADAY_ORDER.get(Action(str(action)), _INTRADAY_DEFAULT)
    except ValueError:
        return _INTRADAY_DEFAULT


# What counts as a trade: the actions that exchange cash for units.
_TRADE_ACTIONS = (Action.BUY, Action.SELL)
# Actions whose `$` is a real denomination of money that moved, attributed to symbol.
DENOMINATED_ACTIONS = (Action.BUY, Action.SELL, Action.DIVIDEND, Action.ROC)

# How far a settlement lag, counted in weekdays, may differ from the account's usual.
SETTLEMENT_LAG_TOLERANCE = 2

# min trades below which no habit can be established for settlement lag.
SETTLEMENT_LAG_MIN_TRADES = 5

# How long after a holding closes a dividend on it is still expected. A
# dividend is earned on the ex-date and paid weeks later, so a sale can leave income
# arriving against a pool holding nothing. This is normal behaviour and should be
# ignored. Sized to cover a quarterly payer's ex-to-pay gap with buffer.
_INCOME_TAIL_DAYS = 60


@dataclass(frozen=True)
class ReplayConfig:
    """Per-account facts the replay needs, resolved before it starts.

    Attributes:
        accounts: Each account, with its type, whether it is margin, and its fee
            rules with nothing left on `auto`.
        symbols: Resolver for ticker renames.
    """

    accounts: Mapping[str, Account]
    symbols: SymbolResolver

    @classmethod
    def build(
        cls,
        rows: Sequence[Txn],
        symbols: SymbolResolver,
        accounts: Mapping[str, Account] | None = None,
    ) -> ReplayConfig:
        """Resolve each account and its fee rules for a set of rows.

        Args:
            rows: All transactions about to be replayed.
            symbols: Resolver for ticker renames.
            accounts: Each account as configured, when not read from the config.

        Returns:
            A populated `ReplayConfig`. A fee setting left on `auto` is settled
            from the account's own rows.
        """
        by_account: dict[str, list[Txn]] = {}
        for row in rows:
            by_account.setdefault(row.account, []).append(row)
        known = accounts or {}
        return cls(
            accounts={
                name: replace(
                    account := known.get(name) or resolve_account(name),
                    fees=account.fees.resolve(name, account_rows),
                )
                for name, account_rows in by_account.items()
            },
            symbols=symbols,
        )


class _Pools:
    """The three holding accumulators, one per grain so each can be walked alone."""

    def __init__(self) -> None:
        self._by_grain: dict[Grain, dict[tuple[Pool, str], HoldingState]] = {
            grain: {} for grain in Grain
        }
        self._seen_splits: set[tuple[Pool, str, str, str]] = set()

    def state(self, pool: Pool, symbol: str, currency: Currency) -> HoldingState:
        """Return the state for one pool and symbol, opened in `currency` if new."""
        return self._by_grain[pool.grain].setdefault(
            (pool, symbol),
            HoldingState(symbol, currency),
        )

    def all_states(
        self,
        grain: Grain,
    ) -> Iterable[tuple[tuple[Pool, str], HoldingState]]:
        """Iterate every (pool, symbol) state at one grain."""
        return self._by_grain[grain].items()

    def claim_split(self, key: tuple[Pool, str, str, str]) -> bool:
        """Record a split, returning False when this pool has already had it.

        One corporate action reported by three brokers applies once to the
        pooled type and folio grains, but three times across the three
        account pools: each account really did split.
        """
        if key in self._seen_splits:
            return False
        self._seen_splits.add(key)
        return True


def _snapshot(state: HoldingState, before: HoldingState) -> GrainMeasures:
    """Freeze a pool's figures after a row, alongside the change it caused."""
    usd = state.usd
    return GrainMeasures(
        units=state.units,
        acb_cad=state.acb_cad,
        acb_usd=state.acb_usd if usd else ZERO,
        delta_cad=state.acb_cad - before.acb_cad,
        delta_usd=(state.acb_usd - before.acb_usd) if usd else ZERO,
        avg_cad=safe_div(state.acb_cad, state.units),
        avg_usd=safe_div(state.acb_usd, state.units) if usd else ZERO,
        realized_cad=state.realized_cad,
        realized_usd=state.realized_usd if usd else ZERO,
        gain_cad=state.realized_cad - before.realized_cad,
        gain_usd=(state.realized_usd - before.realized_usd) if usd else ZERO,
        usd=usd,
    )


class _Replay:
    """One replay in progress. Owns the accumulators and the diagnostics."""

    def __init__(
        self,
        rows: Sequence[Txn],
        fx: FxRates,
        cfg: ReplayConfig,
    ) -> None:
        self.rows = rows
        self.fx = fx
        self.cfg = cfg
        self.pools = _Pools()
        self.result = ReplayResult()
        pairs, _unpaired = pair_transfers(rows)
        self.pairs = [self._located(pair) for pair in pairs]
        self.pair_of: dict[int, TransferPair] = {
            leg.txn_id: pair
            for pair in self.pairs
            for leg in (pair.out_leg, pair.in_leg)
        }
        # Cost base carried by an out leg, waiting for its in leg to claim it.
        self.carried: dict[tuple[int, Grain], tuple[Decimal, Decimal]] = {}
        # Track accounts already reported negative, so only the first crossing shows.
        self.reported_negative: set[tuple[str, Currency]] = set()
        # * Split coverage trackers
        # (symbol, transaction date) -> set[account] , accounts that recorded the split.
        self.split_rows: dict[tuple[str, str], set[str]] = {}
        # (symbol, transaction date) -> set[account] , accounts that hold it.
        self.split_holders: dict[tuple[str, str], set[str]] = {}

    def _type_of(self, account: str) -> AccountType:
        """Return an account's type, UNKNOWN when it was never resolved."""
        known = self.cfg.accounts.get(account)
        return known.account_type if known is not None else AccountType.UNKNOWN

    def _located(self, pair: TransferPair) -> TransferPair:
        """Add where a pair crosses pools, and the grains it moves anything at."""
        crossings = self._crossings_of(pair)
        crossed = {crossing.grain for crossing in crossings}
        one_holding = self._touches_one_holding(pair)
        return replace(
            pair,
            crossings=crossings,
            moves_at=tuple(
                grain for grain in Grain if grain in crossed or not one_holding
            ),
        )

    def _crossings_of(self, pair: TransferPair) -> tuple[Crossing, ...]:
        """List the grains where a pair's legs sit in different pools."""
        out_leg, in_leg = pair.out_leg, pair.in_leg
        out_type = self._type_of(out_leg.account)
        in_type = self._type_of(in_leg.account)
        crossings = []
        for grain in Grain:
            out_pool = Pool.of(grain, out_leg, out_type)
            in_pool = Pool.of(grain, in_leg, in_type)
            if out_pool != in_pool:
                crossings.append(Crossing(out_pool, in_pool))
        return tuple(crossings)

    def _touches_one_holding(self, pair: TransferPair) -> bool:
        """Whether both legs touch the same symbol, or for cash the same currency.

        At a grain where such a pair stays inside one pool, the units or cash never
        leave it, so applying each leg on its own date would only drop them from it
        between the two. A journal stays in one pool but changes symbol or currency,
        so it still moves.
        """
        out_leg, in_leg = pair.out_leg, pair.in_leg
        if not pair.moves_units:
            return out_leg.currency is in_leg.currency
        return self._symbol_of(out_leg) == self._symbol_of(in_leg)

    def _symbol_of(self, row: Txn) -> str | None:
        """Resolve a row's ticker to its symbol on its own date, if it has one."""
        return (
            self.cfg.symbols.canonical(row.ticker, row.txn_date) if row.ticker else None
        )

    def run(self) -> ReplayResult:
        """Walk the ledger and return everything it produced."""
        self._diagnose_unknown_accounts()
        self._acb_walk()
        self._cash_walk()
        self._final_units_check()
        self._split_coverage_check()
        self._mixed_currency_check()
        self._settlement_lag_check()
        self._superficial_loss_check()
        self._transfer_dates_check()
        self._transfer_types_check()
        self._attach_flags()
        self.result.holdings = self._holdings_by_pool()
        return self.result

    # -- DIAGNOSTICS ------------------------------------------------------

    def diagnose(  # noqa: PLR0913 - every argument names one facet of the diagnostic
        self,
        code: DiagnosticCode,
        row: Txn | None = None,
        pool: Pool | None = None,
        detail: str = "",
        *,
        account: str | None = None,
        symbol: str | None = None,
        currency: Currency | None = None,
        as_of: str | None = None,
        value: Decimal | None = None,
    ) -> None:
        """Record a diagnostic."""
        txn_id = row.txn_id if row is not None else None
        self.result.diagnostics.append(
            Diagnostic(
                code=code,
                txn_id=txn_id,
                pool=pool,
                detail=detail,
                account=account or (row.account if row is not None else None),
                symbol=symbol,
                currency=currency,
                as_of=as_of,
                value=value,
            ),
        )

    def _attach_flags(self) -> None:
        """Tag each ledger row with every code raised against it.

        Runs last, because the cash walk and the end-of-replay checks raise
        codes long after the row that carries them was emitted.
        """
        by_txn = self.result.codes_by_txn()
        self.result.rows = [
            replace(computed, flags=by_txn[computed.row.txn_id])
            if computed.row.txn_id in by_txn
            else computed
            for computed in self.result.rows
        ]

    def _flag_holdings(self) -> None:
        """Roll each row's flags up to every pool holding it, in `TxnId` order."""
        for computed in sorted(self.result.rows, key=lambda c: c.row.txn_id):
            if computed.symbol is None or not computed.flags:
                continue
            for grain in Grain:
                pool = Pool.of(grain, computed.row, computed.acct_type)
                state = self.pools.state(pool, computed.symbol, computed.row.currency)
                state.flags += tuple(
                    code for code in computed.flags if code not in state.flags
                )

    def _holdings_by_pool(self) -> dict[Pool, tuple[HoldingState, ...]]:
        """Hand over every pool's holdings, each pool's sorted by symbol."""
        self._flag_holdings()
        by_pool: dict[Pool, list[HoldingState]] = {}
        for grain in Grain:
            ordered = sorted(self.pools.all_states(grain), key=lambda item: item[0])
            for (pool, _symbol), state in ordered:
                by_pool.setdefault(pool, []).append(state)
        return {pool: tuple(states) for pool, states in by_pool.items()}

    def _diagnose_unknown_accounts(self) -> None:
        """Report accounts whose account type could not be inferred."""
        for account, known in sorted(self.cfg.accounts.items()):
            if known.account_type is AccountType.UNKNOWN:
                self.diagnose(
                    DiagnosticCode.UNKNOWN_ACCOUNT_TYPE,
                    pool=Pool(Grain.ACCOUNT, account),
                    detail=f"Could not infer an account type for account '{account}'",
                    account=account,
                )

    # -- CURRENCY SHORTCUTS -----------------------------------------------

    def to_cad(self, amount: Decimal, row: Txn) -> Decimal:
        """Convert a row amount into CAD at that row's settlement-date rate."""
        return self.fx.to_cad(amount, row.fx_date, row.currency).value

    def rate_for(self, row: Txn) -> tuple[Decimal | None, str | None]:
        """Return the USDCAD rate applied to a row, or None for a CAD row."""
        if row.currency is Currency.CAD:
            return None, None
        conversion = self.fx.to_cad(ONE, row.fx_date, row.currency)
        return conversion.rate, conversion.rate_date

    # -- THE COST-BASE WALK -----------------------------------------------

    def _acb_walk(self) -> None:
        """Apply every row to all three pools, in transaction-date order."""
        ordered = sorted(
            self.rows,
            key=lambda row: (
                row.txn_date,
                intraday_rank(row.action),
                row.txn_id,
            ),
        )
        self.result.rows.extend(self._apply(row) for row in ordered)

    def _apply(self, row: Txn) -> LedgerRow:
        """Apply one row to all three pools and emit a ledger row."""
        acct_type = self._type_of(row.account)
        symbol = self._symbol_of(row)
        rate, rate_date = self.rate_for(row)
        self._row_checks(row)
        if row.action is Action.SPLIT and symbol is not None:
            # Record the split against the holding pool.
            self._record_split_coverage(row, symbol)

        dividend_cad, dividend_usd = self._dividend(row)
        measures: dict[Grain, GrainMeasures] = {}
        for grain in Grain:
            if symbol is None:
                measures[grain] = GrainMeasures()
                continue
            pool = Pool.of(grain, row, acct_type)
            state = self.pools.state(pool, symbol, row.currency)
            before = replace(state)
            self._apply_to_pool(row, pool, symbol, state)
            state.dividends_cad += dividend_cad or ZERO
            if state.usd:
                state.dividends_usd += dividend_usd or ZERO
            measures[grain] = _snapshot(state, before)

        proceeds_cad, proceeds_usd = self._proceeds(row)
        self.result.totals.add((row.action, row.account, symbol or "", row.currency))

        return LedgerRow(
            row=row,
            symbol=symbol,
            acct_type=acct_type,
            impact=row.impact,
            fx_rate=rate,
            fx_date=rate_date,
            proceeds_cad=proceeds_cad,
            proceeds_usd=proceeds_usd,
            dividend_cad=dividend_cad,
            dividend_usd=dividend_usd,
            acct=measures[Grain.ACCOUNT],
            type=measures[Grain.TYPE],
            folio=measures[Grain.FOLIO],
        )

    def _row_checks(self, row: Txn) -> None:
        """Diagnostics that read a row on its own, with no pool state."""
        if (
            row.action in _TRADE_ACTIONS
            and row.settle_date
            and row.txn_date
            and row.settle_date < row.txn_date
        ):
            self.diagnose(
                DiagnosticCode.SETTLEMENT_BEFORE_TRADE,
                row,
                detail=f"Settles {row.settle_date}, traded {row.txn_date}",
            )
        if row.action is Action.FXT:
            self._check_fxt(row)
        elif row.action in TRANSFER_ACTIONS:
            # Checked here rather than in the pool walk, to also capture cash transfers
            # that carry no ticker.
            if row.txn_id not in self.pair_of:
                self.diagnose(
                    DiagnosticCode.TRANSFER_UNPAIRED,
                    row,
                    detail=f"{row.action} has no matching counterpart leg",
                )
        elif row.action in _TRADE_ACTIONS and row.fee:
            self._check_fee(row)

    def _check_fee(self, row: Txn) -> None:
        """Report a trade whose amount the account's fee rules cannot account for.

        A trade matching neither convention is ambiguous. One matching only the
        other convention than the account's goes against it.
        """
        fees = self._fees(row)
        if not fees.reconciles(row):
            self.diagnose(
                DiagnosticCode.AMBIGUOUS_FEE_CONVENTION,
                row,
                detail=(
                    f"Amount {row.amount or ZERO} matches neither Price*Units nor "
                    f"Price*Units +/- Fee {row.fee or ZERO}"
                ),
            )
            return
        pointed = fees.other_convention(row)
        if pointed is not None:
            self.diagnose(
                DiagnosticCode.FEE_CONVENTION_MISMATCH,
                row,
                detail=(
                    f"Amount {row.amount or ZERO} fits {pointed}, but "
                    f"{row.account} uses {fees.convention}"
                ),
            )

    def _apply_to_pool(
        self,
        row: Txn,
        pool: Pool,
        symbol: str,
        state: HoldingState,
    ) -> None:
        """Dispatch one row against one pool's holding."""
        # Read before dispatch: the row that closes a holding is itself a day
        # the pool still held it, right up to the moment it sold.
        held_before = state.units > ZERO

        if row.action is Action.BUY:
            self._apply_buy(row, state)
        elif row.action is Action.SELL:
            self._apply_sell(row, pool, symbol, state)
        elif row.action is Action.ROC:
            self._apply_roc(row, pool, symbol, state)
        elif row.action is Action.SPLIT:
            self._apply_split(row, pool, symbol, state)
        elif row.action in TRANSFER_ACTIONS:
            self._apply_transfer(row, pool.grain, state)
        elif (
            row.action is Action.DIVIDEND
            and self._income_is_orphaned(row, state)
            and pool.grain is Grain.ACCOUNT
        ):
            # Account grain only: the pooled grains have no holding either, and
            # three copies of one diagnostic is noise, not extra information.
            self.diagnose(
                DiagnosticCode.INCOME_WITHOUT_HOLDING,
                row,
                pool=pool,
                detail=f"Dividend on {symbol} with no holding in {pool.label}",
                symbol=symbol,
            )

        # Recorded after the checks above, so a dividend is judged against
        # the holding as it stood before this row.
        if held_before or state.units > ZERO:
            state.last_held = row.txn_date

    def _income_is_orphaned(self, row: Txn, state: HoldingState) -> bool:
        """Whether income arrived against a pool that has no claim to it."""
        if state.units != ZERO:
            return False
        if state.last_held is None:
            return True
        cutoff = date.fromisoformat(row.txn_date) - timedelta(days=_INCOME_TAIL_DAYS)
        return state.last_held < cutoff.isoformat()

    def _apply_buy(self, row: Txn, state: HoldingState) -> None:
        """Add units and the full cost, commission included."""
        trade: TradeCash = self._trade(row)
        state.units += abs(row.units or ZERO)
        state.acb_cad += self.to_cad(trade.cost, row)
        if row.currency is Currency.USD:
            state.acb_usd += trade.cost

    def _apply_sell(
        self,
        row: Txn,
        pool: Pool,
        symbol: str,
        state: HoldingState,
    ) -> None:
        """Remove a proportional slice of the cost base and realize the gain.

        The CAD cost base comes off *proportionally*, not by converting the
        removed native amount. Proceeds, by contrast, are converted at the
        settlement-date rate. The gap between the two is the FX component of the
        realized gain, which is exactly what CRA taxes.
        """
        trade: TradeCash = self._trade(row)
        sold = abs(row.units or ZERO)
        held = state.units

        if held <= ZERO or sold > held:
            self.diagnose(
                DiagnosticCode.OVERSELL,
                row,
                pool=pool,
                detail=f"Sold {sold} against {held} held in {pool.label}",
                symbol=symbol,
                value=held,
            )
            # Take whatever cost base is left; units are allowed to go negative
            # so the arithmetic stays recoverable once the missing row appears.
            fraction = ONE
        else:
            fraction = safe_div(sold, held)

        removed_cad = state.acb_cad * fraction
        removed_usd = state.acb_usd * fraction

        state.units -= sold
        state.acb_cad -= removed_cad
        state.acb_usd -= removed_usd
        state.realized_cad += self.to_cad(trade.proceeds, row) - removed_cad
        if row.currency is Currency.USD:
            state.realized_usd += trade.proceeds - removed_usd

        self._snap_dust(state)

    def _apply_roc(
        self,
        row: Txn,
        pool: Pool,
        symbol: str,
        state: HoldingState,
    ) -> None:
        """Reduce the cost base by a return of capital. No cash moves."""
        native = abs(row.amount or ZERO)
        roc_cad = self.to_cad(native, row)

        # * Max ACB we can remove is the current ACB itself, but not negative.
        applied_cad = min(roc_cad, max(state.acb_cad, ZERO))
        state.acb_cad -= applied_cad
        excess_cad = roc_cad - applied_cad
        state.realized_cad += excess_cad

        if row.currency is Currency.USD:
            applied_usd = min(native, max(state.acb_usd, ZERO))
            state.acb_usd -= applied_usd
            state.realized_usd += native - applied_usd

        if excess_cad > ZERO:
            self.diagnose(
                DiagnosticCode.ROC_EXCEEDS_ACB,
                row,
                pool=pool,
                detail=(
                    f"{excess_cad} of return of capital exceeds cost base in "
                    f"{pool.label}"
                ),
                symbol=symbol,
                value=excess_cad,
            )
        if self._income_is_orphaned(row, state) and pool.grain is Grain.ACCOUNT:
            self.diagnose(
                DiagnosticCode.INCOME_WITHOUT_HOLDING,
                row,
                pool=pool,
                detail=f"Return of capital with no holding in {pool.label}",
                symbol=symbol,
            )

    def _apply_split(
        self,
        row: Txn,
        pool: Pool,
        symbol: str,
        state: HoldingState,
    ) -> None:
        """Scale a holding's units by the split ratio. The ACB is untouched.

        `Price` is units before and `Units` units after, so a 1:10 split
        multiplies units by ten and divides average cost by ten while the total
        stays byte-identical. `Amount` on a SPLIT row is junk.
        """
        price, units = row.price or ZERO, row.units or ZERO
        if price <= ZERO or units <= ZERO:
            # Sign rules flip a negative ratio, but a zero passes them (it has no
            # sign to flip), so skip rather than divide by it.
            logger.debug("Skipping SPLIT %d: ratio is not positive", row.txn_id)
            return

        ratio = units / price
        if not self.pools.claim_split((pool, symbol, row.txn_date, str(ratio))):
            if pool.grain is Grain.ACCOUNT:
                self.diagnose(
                    DiagnosticCode.DUPLICATE_SPLIT,
                    row,
                    pool=pool,
                    detail=(
                        f"{symbol} already split {ratio} on {row.txn_date} "
                        f"in {pool.label}"
                    ),
                    symbol=symbol,
                    as_of=row.txn_date,
                )
            return

        if state.units == ZERO:
            if pool.grain is Grain.ACCOUNT:
                self.diagnose(
                    DiagnosticCode.SPLIT_WITHOUT_HOLDING,
                    row,
                    pool=pool,
                    detail=f"Split of {symbol} with no holding in {pool.label}",
                    symbol=symbol,
                    as_of=row.txn_date,
                )
            return

        state.units *= ratio

    def _apply_transfer(
        self,
        row: Txn,
        grain: Grain,
        state: HoldingState,
    ) -> None:
        """Move units and cost base between pools without realizing a gain."""
        pair = self.pair_of.get(row.txn_id)
        if pair is None:
            # Already reported by `_row_checks`; units still have to move, but
            # with no counterpart there is nowhere for the cost base to go.
            self._move_units_only(row, state)
            return
        if grain not in pair.moves_at:
            return

        if row.action is Action.TFR_OUT:
            self._transfer_out(row, grain, state, pair.pair_id)
        else:
            self._transfer_in(row, grain, state, pair.pair_id)

    def _move_units_only(self, row: Txn, state: HoldingState) -> None:
        """Apply an unpaired leg: units move, the cost base cannot follow them."""
        if not row.is_in_kind_transfer:
            return
        if row.action is Action.TFR_OUT:
            state.units -= abs(row.units or ZERO)
        else:
            state.units += abs(row.units or ZERO)
        self._snap_dust(state)

    def _transfer_out(
        self,
        row: Txn,
        grain: Grain,
        state: HoldingState,
        pair_id: int,
    ) -> None:
        """Remove units and their share of the cost base, holding it for the in leg."""
        if not row.is_in_kind_transfer:
            self.carried[(pair_id, grain)] = (ZERO, ZERO)
            return

        moved = abs(row.units or ZERO)
        held = state.units
        fraction = ONE if held <= ZERO or moved > held else safe_div(moved, held)
        moved_cad = state.acb_cad * fraction
        moved_native = state.acb_usd * fraction

        state.units -= moved
        state.acb_cad -= moved_cad
        state.acb_usd -= moved_native
        self._snap_dust(state)
        self.carried[(pair_id, grain)] = (moved_cad, moved_native)

    def _transfer_in(
        self,
        row: Txn,
        grain: Grain,
        state: HoldingState,
        pair_id: int,
    ) -> None:
        """Add units and the cost base the out leg handed over."""
        if not row.is_in_kind_transfer:
            return

        moved_cad, moved_native = self.carried.get((pair_id, grain), (ZERO, ZERO))
        state.units += abs(row.units or ZERO)
        state.acb_cad += moved_cad

        if row.currency is Currency.USD:
            # Same currency on both legs carries the native figure intact; a
            # journal has none to carry, so derive it from CAD.
            state.acb_usd += (
                moved_native
                if moved_native != ZERO
                else self.fx.from_cad(moved_cad, row.fx_date, row.currency).value
            )

    def _snap_dust(self, state: HoldingState) -> None:
        """Snap a holding's residual units to zero, rolling leftover cost into gain."""
        if state.units != ZERO and abs(state.units) < DUST_UNITS:
            state.realized_cad -= state.acb_cad
            state.realized_usd -= state.acb_usd
            state.units = ZERO
            state.acb_cad = ZERO
            state.acb_usd = ZERO

    def _fees(self, row: Txn) -> FeeRules:
        """Return the fee rules of a row's account."""
        return self.cfg.accounts[row.account].fees

    def _trade(self, row: Txn) -> TradeCash:
        """Resolve one trade's cost, proceeds and commission."""
        return self._fees(row).trade_cash(row)

    def _check_fxt(self, row: Txn) -> None:
        """Reconcile a single currency conversion against itself."""
        amount, price, units = row.amount or ZERO, row.price or ZERO, row.units or ZERO
        if ZERO in (price, units):
            return
        implied = abs(units * price)
        if abs(abs(amount) - implied) > FXT_TOLERANCE:
            self.diagnose(
                DiagnosticCode.FXT_AMOUNT_INCONSISTENT,
                row,
                detail=f"Amount {amount} against Units*Price {implied}",
            )

    def _proceeds(self, row: Txn) -> tuple[Decimal | None, Decimal | None]:
        """Sale proceeds, reported apart from the gain on Schedule 3."""
        if row.action is not Action.SELL:
            return None, None
        trade = self._trade(row)
        native = trade.proceeds if row.currency is Currency.USD else None
        return self.to_cad(trade.proceeds, row), native

    def _dividend(self, row: Txn) -> tuple[Decimal | None, Decimal | None]:
        """Dividend income, converted because it is reported in CAD.

        Amounts are never coerced positive: IBKR issues reversals as a negative
        dividend paired with a corrected positive one.
        """
        if row.action is not Action.DIVIDEND:
            return None, None
        amount = row.amount or ZERO
        native = amount if row.currency is Currency.USD else None
        return self.to_cad(amount, row), native

    # -- THE CASH WALK ----------------------------------------------------

    def _settlement_order(self, row: Txn) -> tuple[str, int, bool]:
        """Order the cash walk by date, an in leg directly after its own out leg.

        A TxnId reflects only which broker's rows were imported first. Directly
        after its out leg rather than last on the date, so a transfer passing
        through an account is measured against the money that reached it.
        """
        place = row.txn_id
        arriving = row.action is Action.TFR_IN
        pair = self.pair_of.get(row.txn_id) if arriving else None
        if pair is not None and pair.out_leg.settle_date == row.settle_date:
            place = pair.out_leg.txn_id
        return row.settle_date, place, arriving

    def _cash_walk(self) -> None:
        """Accumulate cash per pool and currency, in settlement-date order."""
        ordered = sorted(
            self.result.rows,
            key=lambda computed: self._settlement_order(computed.row),
        )
        net_deposits = NetDeposits(
            self.result.rows,
            self.pair_of,
            self.fx,
        )
        pending: list[Txn] = []
        current_date = ""

        for computed in ordered:
            row = computed.row
            if row.settle_date != current_date and pending:
                self._check_negative_cash(pending, current_date)
                pending = []
            current_date = row.settle_date
            effects = self._cash_effects(row)
            net_deposits.apply(computed, effects)
            self._book(computed, effects)
            pending.append(row)
        if pending:
            self._check_negative_cash(pending, current_date)

        self._roll_up_realized_gain()
        for (pool, currency), totals in net_deposits.transfer_totals().items():
            cash = self._cash(pool, currency)
            cash.transfers = totals.transfers
            cash.transfers_value = totals.transfers_value
        self.result.deposit_shares = net_deposits.shares

    def _cash(self, pool: Pool, currency: Currency) -> CashState:
        """Return the cash state for one pool and currency, creating it if new."""
        return self.result.cash.setdefault((pool, currency), CashState())

    def _moving_grains(self, row: Txn) -> tuple[Grain, ...] | type[Grain]:
        """Return the grains a transfer leg moves cash at: all three if unpaired."""
        pair = self.pair_of.get(row.txn_id)
        return Grain if pair is None else pair.moves_at

    def _book(self, computed: LedgerRow, effects: Iterable[CashEffect]) -> None:
        """Apply a row's cash effects to the pools at the grains each moves at."""
        row = computed.row
        for effect in effects:
            for grain in Grain:
                # Opened even where a transfer stays put, so the pool still
                # reports its (unchanged) cash.
                state = self._cash(
                    Pool.of(grain, row, computed.acct_type),
                    effect.currency,
                )
                if grain not in effect.grains:
                    continue
                state.cash += effect.cash
                state.contributions += effect.contributions
                state.withdrawals += effect.withdrawals
                state.dividends += effect.dividends
                state.fees += effect.fees

    def _cash_effects(self, row: Txn) -> tuple[CashEffect, ...]:
        """Work out what one row does to cash.

        `ROC` and `SPLIT` have none: a return of capital reclassifies a distribution
        that was already paid as a dividend, so counting it as cash double-counts the
        money, and a split moves no money.
        """
        amount = row.amount or ZERO
        currency = row.currency
        effect: CashEffect | None = None
        if row.action in _TRADE_ACTIONS:
            trade: TradeCash = self._trade(row)
            cash = -trade.cost if row.action is Action.BUY else trade.proceeds
            effect = CashEffect(currency, cash=cash, fees=trade.commission)
        elif row.action is Action.DIVIDEND:
            effect = CashEffect(currency, cash=amount, dividends=amount)
        elif row.action is Action.FCH:
            # When row amount negative, the charged fee (abs) is recorded.
            charged = -amount if amount < ZERO else ZERO
            effect = CashEffect(currency, cash=amount, fees=charged)
        elif row.action is Action.CONTRIBUTION:
            effect = CashEffect(currency, cash=amount, contributions=abs(amount))
        elif row.action is Action.WITHDRAWAL:
            effect = CashEffect(currency, cash=amount, withdrawals=abs(amount))
        elif row.action is Action.FXT:
            return self._fxt_effects(row)
        elif row.action in TRANSFER_ACTIONS and not row.is_in_kind_transfer:
            # Real cash moves, but how much of it counts as the holder's own money
            # is not its face value: that is left to the net deposits tracker.
            effect = CashEffect(
                currency,
                cash=amount,
                grains=self._moving_grains(row),
            )
        return () if effect is None else (effect,)

    def _fxt_effects(self, row: Txn) -> tuple[CashEffect, ...]:
        """Work out a currency conversion, in whichever of its two shapes it is.

        A single-leg (IBKR) row carries the CAD delta in `Amount`, the USD delta
        in `Units` and the rate in `Price`. A two-leg (QuestTrade) row has no
        `Price` or `Units`, and its `Amount` applies to its own currency.

        The IBKR FX commission is a separate debit charged in USD. `Units` is
        the gross USD received, so the fee comes off on top of it, whatever the
        row's own `$` says.
        """
        commission = self._fees(row).commission(row)
        amount, price, units = row.amount or ZERO, row.price or ZERO, row.units or ZERO
        if ZERO in (price, units):
            cash = amount - commission
            return (CashEffect(row.currency, cash=cash, fees=commission),)

        return (
            CashEffect(Currency.CAD, cash=amount),
            CashEffect(Currency.USD, cash=units - commission, fees=commission),
        )

    def _roll_up_realized_gain(self) -> None:
        """Carry realized gains from the holding states onto the cash states.

        Gains land in the holding's own currency, matching every other figure a
        `CashState` carries. The CAD-converted view lives on the ledger.
        """
        for grain in Grain:
            for (pool, _symbol), state in self.pools.all_states(grain):
                if state.currency is Currency.USD:
                    bucket = self._cash(pool, Currency.USD)
                    bucket.realized_gain += state.realized_usd
                else:
                    bucket = self._cash(pool, Currency.CAD)
                    bucket.realized_gain += state.realized_cad

    def _check_negative_cash(self, rows: Sequence[Txn], on: str) -> None:
        """Report the first date an account's cash crosses below zero.

        Negative cash needs no external balance to detect and catches a missing
        transaction immediately. Only account pools are judged: a pooled type or
        folio balance nets accounts against each another and says nothing
        about a missing row.

        **Judged to the cent**, which is the unit cash is denominated in and the
        precision every other reading of a balance already uses.
        """
        for account in sorted({row.account for row in rows}):
            if self.cfg.accounts[account].margin:
                continue
            for currency in (Currency.CAD, Currency.USD):
                if (account, currency) in self.reported_negative:
                    continue
                state = self.result.cash.get((Pool(Grain.ACCOUNT, account), currency))
                if state is None or q2(state.cash) >= ZERO:
                    continue
                self.reported_negative.add((account, currency))
                first = next(row for row in rows if row.account == account)
                self.diagnose(
                    DiagnosticCode.CASH_NEGATIVE,
                    first,
                    pool=Pool(Grain.ACCOUNT, account),
                    detail=f"{account} {currency} cash is {state.cash} as of {on}",
                    account=account,
                    currency=currency,
                    as_of=on,
                    value=state.cash,
                )

    # -- FINAL CHECKS -----------------------------------------------------

    def _final_units_check(self) -> None:
        """Report any pool that ends below zero units."""
        for grain in Grain:
            for (pool, symbol), state in self.pools.all_states(grain):
                if state.units < -DUST_UNITS:
                    self.diagnose(
                        DiagnosticCode.NEGATIVE_FINAL_UNITS,
                        pool=pool,
                        detail=f"{symbol} ends at {state.units} units in {pool.label}",
                        account=pool.name if grain is Grain.ACCOUNT else None,
                        symbol=symbol,
                        value=state.units,
                    )

    def _record_split_coverage(self, row: Txn, symbol: str) -> None:
        """Note who recorded one split, and who was holding when it happened."""
        key = (symbol, row.txn_date)
        self.split_rows.setdefault(key, set()).add(row.account)
        if key not in self.split_holders:
            self.split_holders[key] = {
                pool.name
                for (pool, held), state in self.pools.all_states(Grain.ACCOUNT)
                if held == symbol and state.units > ZERO
            }

    def _split_coverage_check(self) -> None:
        """Report accounts that held a security through a split it never got.

        A split was recorded on an account, but there exists other accounts
        that hold the security after the split, without having a split row.
        """
        for (symbol, on), holders in sorted(self.split_holders.items()):
            recorded = self.split_rows.get((symbol, on), set())
            for account in sorted(holders - recorded):
                self.diagnose(
                    DiagnosticCode.SPLIT_MISSING_ON_ACCOUNT,
                    pool=Pool(Grain.ACCOUNT, account),
                    detail=f"Split on {symbol} missing on account {account}",
                    account=account,
                    symbol=symbol,
                    as_of=on,
                )

    def _mixed_currency_check(self) -> None:
        """Report a security whose rows are not all in one currency.

        A dual listing has its own symbol on each exchange, so one security
        booked in two currencies is a data error nearly every time.
        """
        seen: dict[str, dict[Currency, list[Txn]]] = {}
        for computed in self.result.rows:
            if computed.symbol is None or computed.row.action not in (
                DENOMINATED_ACTIONS
            ):
                continue
            by_currency = seen.setdefault(computed.symbol, {})
            by_currency.setdefault(computed.row.currency, []).append(computed.row)

        for symbol, by_currency in sorted(seen.items()):
            if len(by_currency) < 2:  # noqa: PLR2004 - two currencies is the finding
                continue
            ranked = sorted(
                by_currency.items(),
                key=lambda pair: (-len(pair[1]), str(pair[0])),
            )
            main = ranked[0][0]
            for currency, rows in ranked[1:]:
                for row in rows:
                    self.diagnose(
                        DiagnosticCode.MIXED_CURRENCY,
                        row,
                        detail=(
                            f"{symbol} is booked in {currency} here and in "
                            f"{main} on {len(ranked[0][1])} other row(s)"
                        ),
                        symbol=symbol,
                        currency=currency,
                    )

    def _settlement_lag_check(self) -> None:
        """Report a trade settling far outside its account's usual lag.

        The typical settlement lag is calculated for the account, and transactions
        outside it are reported. Lags count weekdays, not trading days, because the
        replay never loads an exchange calendar.
        """
        lags: dict[str, list[tuple[Txn, int]]] = {}
        for row in self.rows:
            if row.action not in _TRADE_ACTIONS or not row.settle_date:
                continue
            if row.settle_date < row.txn_date:
                # Already reported under different code
                continue
            lag = _weekdays_between(row.txn_date, row.settle_date)
            lags.setdefault(row.account, []).append((row, lag))

        for account, entries in sorted(lags.items()):
            if len(entries) < SETTLEMENT_LAG_MIN_TRADES:
                continue
            usual = Counter(lag for _row, lag in entries).most_common(1)[0][0]
            for row, lag in entries:
                if abs(lag - usual) > SETTLEMENT_LAG_TOLERANCE:
                    self.diagnose(
                        DiagnosticCode.SETTLEMENT_LAG_OUTLIER,
                        row,
                        detail=(
                            f"Settles {lag} weekdays after the trade; "
                            f"{account} usually takes {usual}"
                        ),
                        value=Decimal(lag),
                    )

    def _superficial_loss_check(self) -> None:
        """Flag a realized loss with a buy of the same symbol in the CRA window.

        A flag only, deliberately: cost base and gain are NOT adjusted
        for it, by design, so units/ACB/avg cost keep matching a broker's own
        figures 1:1.

        The CRA superficial-loss window spans from 30 days before
        the sale to 30 days after it (inclusive of the sale date). Restricted
        to taxable accounts, the only place the rule has any consequence, and
        unknown ones, which might turn out taxable. The triggering buy may be
        in any account.
        """
        buys: dict[str, list[date]] = {}
        for computed in self.result.rows:
            if computed.row.action is Action.BUY and computed.symbol:
                buys.setdefault(computed.symbol, []).append(
                    date.fromisoformat(computed.row.txn_date),
                )
        # Sort the dates per symbol so they can be binary-searched.
        for dates in buys.values():
            dates.sort()

        for computed in self.result.rows:
            row = computed.row
            if row.action is not Action.SELL or not computed.symbol:
                continue
            if (
                self._type_of(row.account) not in SUPERFICIAL_LOSS_TYPES
                or _sale_gain(computed) >= ZERO
            ):
                continue
            sold_on = date.fromisoformat(row.txn_date)
            window_start = sold_on - timedelta(days=SUPERFICIAL_LOSS_DAYS)
            window_end = sold_on + timedelta(days=SUPERFICIAL_LOSS_DAYS)
            repurchased = buys.get(computed.symbol, ())
            # The first purchase not before the window; inside it iff not past
            # the window's end.
            index = bisect_left(repurchased, window_start)
            if index < len(repurchased) and repurchased[index] <= window_end:
                self.diagnose(
                    DiagnosticCode.SUPERFICIAL_LOSS_SUSPECT,
                    row,
                    detail=(
                        f"Loss on {computed.symbol} with a buy within "
                        f"{SUPERFICIAL_LOSS_DAYS} days"
                    ),
                )

    def _transfer_dates_check(self) -> None:
        """Flag transfers whose two legs are dated so figures go wrong.

        Either case is fixed by giving the later leg the earlier leg's transaction
        and settlement date, so the later leg is the one tagged:

        - An in leg dated or settling before its out leg is applied first. Dated
          first, an in-kind leg's cost base is lost. Either way, the sending pool
          is measured wrongly for any transfer out of it until the out leg arrives.
        - In-kind legs in different calendar years leave the units in neither
          account at the year end, so neither account's year-end figures count them.
        """
        for pair in self.pairs:
            out_leg, in_leg = pair.out_leg, pair.in_leg
            if (
                in_leg.txn_date < out_leg.txn_date
                or in_leg.settle_date < out_leg.settle_date
            ):
                code, later, earlier = (
                    DiagnosticCode.TRANSFER_IN_BEFORE_OUT,
                    out_leg,
                    in_leg,
                )
            elif (
                pair.moves_units
                and out_leg.account != in_leg.account
                and out_leg.txn_date[:4] != in_leg.txn_date[:4]
            ):
                code, later, earlier = (
                    DiagnosticCode.TRANSFER_ACROSS_YEAR_END,
                    in_leg,
                    out_leg,
                )
            else:
                continue
            self.diagnose(
                code,
                later,
                detail=(
                    f"{later.action} on {later.txn_date}, its {earlier.action} "
                    f"(TxnId {earlier.txn_id}, {earlier.account}) on "
                    f"{earlier.txn_date}"
                ),
                symbol=self._symbol_of(later),
                as_of=earlier.txn_date,
            )

    def _transfer_types_check(self) -> None:
        """Flag transfers whose two legs sit in different account types.

        Moving cash or units between tax treatments is not a transfer but a
        sale, a withdrawal, a contribution and a buy. Report only: the pair
        still replays as a transfer. Unknown accounts count as one type of
        their own, apart from every known type, since their tax treatment
        can't be told yet. The in leg is tagged,
        and the diagnostic names the out leg's account.
        """
        for pair in self.pairs:
            out_leg, in_leg = pair.out_leg, pair.in_leg
            sent, received = (
                self._type_of(out_leg.account),
                self._type_of(in_leg.account),
            )
            if sent is received:
                continue
            self.diagnose(
                DiagnosticCode.TRANSFER_ACROSS_TYPES,
                in_leg,
                detail=(
                    f"{in_leg.account} ({received}) received from "
                    f"{out_leg.account} ({sent}), TxnId {out_leg.txn_id}"
                ),
                account=out_leg.account,
                symbol=self._symbol_of(in_leg),
                as_of=out_leg.txn_date,
            )


def _weekdays_between(start: str, end: str) -> int:
    """Count weekdays between two `YYYY-MM-DD` dates, excluding the first.

    Args:
        start: The earlier date.
        end: The later date.

    Returns:
        How many weekdays fall in `(start, end]`. Any seven consecutive days
        hold exactly five, so only the leftover tail needs walking.
    """
    begin = date.fromisoformat(start)
    weeks, remainder = divmod((date.fromisoformat(end) - begin).days, 7)
    tail = sum(
        1
        for offset in range(1, remainder + 1)
        if (begin + timedelta(days=offset)).weekday() < WEEKDAYS
    )
    return weeks * WEEKDAYS + tail


def _sale_gain(computed: LedgerRow) -> Decimal:
    """Compute the gain one SELL realized in its own account, in CAD."""
    # cost base removed is negative, so negated delta_cad is the positive acb removed
    removed = -computed.acct.delta_cad
    # gains = total proceeds - cost base removed
    return (computed.proceeds_cad or ZERO) - removed


def replay(rows: Sequence[Txn], fx: FxRates, cfg: ReplayConfig) -> ReplayResult:
    """Replay the ledger into units, cost base, gains and cash.

    Args:
        rows: Every transaction to replay (`Txn`s).
        fx: FX rates for currency conversion.
        cfg: Replay configuration: per-account types, fee conventions and symbol
            resolution.

    Returns:
        One ledger row per transaction, the diagnostics raised, and running cash
        per pool grain, pool and currency.
    """
    return _Replay(rows, fx, cfg).run()
