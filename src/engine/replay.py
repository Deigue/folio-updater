"""The cost-base replay.

Walks the ledger and produces units, adjusted cost base, realized gains and cash
for all three pool grains at once: account, account type, and the whole
portfolio, via one pass.

**Two orderings, one call.** Units and cost base follow the *trade* date; cash
follows the *settle* date. Ordering the cash replay by trade date produces
phantom negative balances whenever a buy and the contribution funding it land on
the same day. Both walks share the row list and every accumulator, so all three scopes
still come out of one pass over each ordering rather than three separate replays.

Six core rules followed:

1. `ROC` moves no cash, it reclassifies a distribution already paid.
2. Order the cash replay by settle date.
3. Auto-detect the fee convention per account.
4. Two `FXT` shapes exist, distinguishable from the row itself.
5. Carry the cost base across a `TFR_IN`/`TFR_OUT` pair, never reading `Amount`.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from engine.accounts import fee_convention_for, resolve_account_type
from engine.events import (
    CashKey,
    CashState,
    ComputedRow,
    PositionState,
    ReplayResult,
    ReplayWarning,
    ScopeMeasures,
)
from engine.transfers import DUST_UNITS, pair_transfers
from utils.constants import (
    TAXABLE_ACCOUNT_TYPES,
    AccountType,
    Action,
    Currency,
    FeeConvention,
    Scope,
    WarningCode,
)
from utils.numeric import ZERO, q2, safe_div

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from engine.events import TxnRow
    from engine.fx_rates import FxRates
    from services.symbols import SymbolResolver

logger = logging.getLogger(__name__)

ONE = Decimal(1)

# The portfolio-wide pool has no natural name.
FOLIO_POOL = "FOLIO"

# A single-leg FXT's Amount, Units and Price are three views of one conversion,
# so they must agree. Half a cent tolerance to capture subcent rounding.
FXT_TOLERANCE = Decimal("0.005")

# Baseline slack when reconciling a trade's Amount against Price * Units.
FEE_MATCH_TOLERANCE = Decimal("0.01")

# Brokers can rounds Price to 2dp, so Price * Units is out by up to half a cent
# per share regardless of fees. Widen tolerance to capture subcent rounding for
# fee convention detection.
PRICE_ROUNDING_PER_UNIT = Decimal("0.005")

# Below this share of the vote, an account's fee convention is ambiguous.
FEE_MAJORITY_SHARE = 0.6

# A realized loss followed by a buy of the same symbol inside this window is a
# superficial-loss candidate. Just a warning flag.
SUPERFICIAL_LOSS_DAYS = 30

# Order actions share a trade date in. The date is the finest resolution the folio
# records, so several rows routinely land on one and their true sequence is not
# recoverable. This prioritization makes things deterministic and avoid cost base
# problems.
#
#   - acquisitions first, so a same-day transfer or sale has something to move
#   - a transfer's out leg before its in leg, so the cost base being carried is
#     known by the time the in leg claims it
#   - sales last, against the largest position, so a same-day buy-then-sell does
#     not read as an oversell.
#
# Actions that dont impact cost base sort last, where their position is irrelevant.
_INTRADAY_ORDER: dict[Action, int] = {
    Action.BUY: 0,
    Action.TFR_OUT: 1,
    Action.TFR_IN: 2,
    Action.SPLIT: 3,
    Action.ROC: 4,
    Action.SELL: 5,
}
_INTRADAY_DEFAULT = 6

# What counts as a trade: the actions that exchange cash for units and so carry
# a real settlement lag.
_TRADE_ACTIONS = (Action.BUY, Action.SELL)

# How long after a position closes a distribution on it is still expected. A
# dividend is earned on the ex-date and paid weeks later, so a sale can leave income
# arriving against a pool holding nothing. This is normal behaviour and should be
# ignored. Sized to cover a quarterly payer's ex-to-pay gap with buffer.
_INCOME_TAIL_DAYS = 60


@dataclass(frozen=True)
class TradeCash:
    """What a trade cost or realized, once the fee is placed correctly.

    Attributes:
        cost: What a BUY added to the cost base -> the amount plus commission.
        proceeds: What a SELL realized -> the amount less commission.
        commission: The commission as a cost: positive when charged, negative on
            the rare row that rebates one.
    """

    cost: Decimal
    proceeds: Decimal
    commission: Decimal


def detect_fee_signs(rows: Sequence[TxnRow]) -> dict[str, Decimal]:
    """Infer which sign each account uses to mean "commission charged".

    Brokers disagree. IBKR and QuestTrade write a charge as a negative number;
    a row typed by hand, and adjustedcostbase.ca's own convention, write it
    positive. Reading only the magnitude would be safe if the two never mixed,
    but they do, since a broker that writes charges negative writes the
    occasional *rebate* positive.

    So the prevailing sign is taken per account, exactly as the fee *inclusion*
    convention is, and a row carrying the minority sign is read as a rebate.

    Args:
        rows: All transactions.

    Returns:
        Per account, the multiplier turning a stored fee into a commission:
        `-1` where charges are written negative, `+1` where they are positive.
        Accounts with no non-zero fee default to `+1`, which is what a
        hand-entered `--fee 4.95` means and is moot when every fee is zero.
    """
    tally: dict[str, int] = {}
    for row in rows:
        if row.fee == ZERO:
            continue
        tally[row.account] = tally.get(row.account, 0) + (1 if row.fee > ZERO else -1)
    return {
        account: (Decimal(-1) if votes < 0 else ONE) for account, votes in tally.items()
    }


def _match_tolerance(units: Decimal) -> Decimal:
    """How far `Price * Units` may sit from `Amount` before it means something."""
    return FEE_MATCH_TOLERANCE + PRICE_ROUNDING_PER_UNIT * abs(units)


def _convention_fits(row: TxnRow, sign: Decimal) -> tuple[bool, bool]:
    """Reconcile one trade against both conventions.

    Args:
        row: A BUY or SELL.
        sign: The account's fee-sign multiplier, from `detect_fee_signs`.

    Returns:
        Whether the row fits EXCLUDED (`Amount` is gross of the fee) and whether
        it fits INCLUDED (`Amount` already contains it). Both are true whenever
        the fee is zero, since the two conventions then coincide.
    """
    commission = row.fee * sign
    amount = abs(row.amount)
    gross = abs(row.price * row.units)
    tolerance = _match_tolerance(row.units)
    included = gross + commission if row.action is Action.BUY else gross - commission
    return (
        abs(amount - gross) <= tolerance,
        abs(amount - included) <= tolerance,
    )


def _classify_row(row: TxnRow, sign: Decimal) -> FeeConvention | None:
    """Which convention a single trade points at, or None when it cannot say."""
    excluded_fits, included_fits = _convention_fits(row, sign)
    if excluded_fits and not included_fits:
        return FeeConvention.EXCLUDED
    if included_fits and not excluded_fits:
        return FeeConvention.INCLUDED
    return None


def resolve_trade_cash(
    row: TxnRow,
    convention: FeeConvention,
    sign: Decimal = ONE,
) -> TradeCash:
    """Resolve trade cashflow and commission based on given information.

    Args:
        row: The trade.
        convention: The account's resolved fee convention. AUTO reconciles the row
            against `Price * Units` on the spot.
        sign: The account's fee-sign multiplier, from `detect_fee_signs`.

    Returns:
        The cost, proceeds and commission for this row.
    """
    commission = row.fee * sign
    amount = abs(row.amount)

    resolved = convention
    if convention is FeeConvention.AUTO:
        resolved = _classify_row(row, sign) or FeeConvention.EXCLUDED

    if resolved is FeeConvention.INCLUDED:
        return TradeCash(cost=amount, proceeds=amount, commission=commission)
    return TradeCash(
        cost=amount + commission,
        proceeds=amount - commission,
        commission=commission,
    )


def detect_fee_conventions(
    rows: Sequence[TxnRow],
    signs: Mapping[str, Decimal] | None = None,
) -> dict[str, FeeConvention]:
    """Infer each account's fee convention from its own trades.

    Every BUY or SELL carrying a non-zero fee is reconciled against
    `Price * Units` and `Price * Units` plus-or-minus the fee, and the majority
    wins for that account. Rows matching both carry no information. Rows
    matching neither abstain and are reported on the row instead.

    Args:
        rows: All transactions.
        signs: Per-account fee-sign multipliers
    Returns:
        One resolved convention per account. Accounts with no usable vote fall
        back to EXCLUDED.
    """
    resolved_signs = dict(signs or detect_fee_signs(rows))
    votes: dict[str, Counter[FeeConvention]] = {}
    for row in rows:
        if row.action not in (Action.BUY, Action.SELL) or row.fee == ZERO:
            continue
        if fee_convention_for(row.account) is not FeeConvention.AUTO:
            continue
        vote = _classify_row(row, resolved_signs.get(row.account, ONE))
        if vote is not None:
            votes.setdefault(row.account, Counter())[vote] += 1

    resolved: dict[str, FeeConvention] = {}
    for account in {row.account for row in rows}:
        configured = fee_convention_for(account)
        if configured is not FeeConvention.AUTO:
            resolved[account] = configured
            continue
        resolved[account] = _majority(account, votes.get(account))
    return resolved


def _majority(account: str, counter: Counter[FeeConvention] | None) -> FeeConvention:
    """Pick an account's convention from its votes, warning on a near-even split."""
    if not counter:
        return FeeConvention.EXCLUDED
    ranked: list[tuple[FeeConvention, int]] = counter.most_common()
    winner, top = ranked[0]
    total = sum(counter.values())
    if total and top / total < FEE_MAJORITY_SHARE:
        logger.warning(
            "Fee convention for '%s' splits near evenly (%s); using %s. Set "
            "accounts.map for that account to settle it explicitly.",
            dict(ranked),
            account,
            winner,
        )
    return winner


@dataclass(frozen=True)
class ReplayConfig:
    """Per-account facts the replay needs, resolved before it starts.

    Attributes:
        account_types: Tax type of each account.
        fee_conventions: Resolved fee convention of each account.
        fee_signs: Fee multiplier turning each account's stored fee into a
            commission. Defaults to positive-means-charged for any account not
            listed, which is what a hand-entered fee means.
        symbols: Resolver for ticker renames.
    """

    account_types: Mapping[str, AccountType]
    fee_conventions: Mapping[str, FeeConvention]
    symbols: SymbolResolver
    fee_signs: Mapping[str, Decimal] = field(default_factory=dict)

    @classmethod
    def build(cls, rows: Sequence[TxnRow], symbols: SymbolResolver) -> ReplayConfig:
        """Resolve account types and fee conventions for a set of rows.

        Args:
            rows: All transactions about to be replayed.
            symbols: Resolver for ticker renames.

        Returns:
            A populated `ReplayConfig`.
        """
        accounts = {row.account for row in rows}
        signs = detect_fee_signs(rows)
        return cls(
            account_types={name: resolve_account_type(name) for name in accounts},
            fee_conventions=detect_fee_conventions(rows, signs),
            symbols=symbols,
            fee_signs=signs,
        )


class _Pools:
    """The three position accumulators, addressed by scope.

    Kept as separate dicts rather than one dict keyed by scope so that an
    account genuinely named `TFSA` can never collide with the TFSA type pool.
    """

    def __init__(self) -> None:
        self._by_scope: dict[Scope, dict[tuple[str, str], PositionState]] = {
            scope: {} for scope in Scope
        }
        self._seen_splits: dict[Scope, set[tuple[str, str, str, str]]] = {
            scope: set() for scope in Scope
        }

    def state(self, scope: Scope, pool: str, symbol: str) -> PositionState:
        """Return the state for one pool and symbol, creating it if new."""
        return self._by_scope[scope].setdefault((pool, symbol), PositionState())

    def all_states(
        self,
        scope: Scope,
    ) -> Iterable[tuple[tuple[str, str], PositionState]]:
        """Iterate every (pool, symbol) state at one grain."""
        return self._by_scope[scope].items()

    def claim_split(self, scope: Scope, key: tuple[str, str, str, str]) -> bool:
        """Record a split, returning False when this pool has already had it.

        One corporate action reported by three brokers applies once to the
        pooled type and portfolio grains, but three times across the three
        account pools: each account really did split.
        """
        seen = self._seen_splits[scope]
        if key in seen:
            return False
        seen.add(key)
        return True


def _pool_key(scope: Scope, row: TxnRow, acct_type: AccountType) -> str:
    """Name the pool a row belongs to at one grain."""
    if scope is Scope.ACCOUNT:
        return row.account
    if scope is Scope.TYPE:
        return str(acct_type)
    return FOLIO_POOL


def _snapshot(state: PositionState, before: PositionState) -> ScopeMeasures:
    """Freeze a pool's figures after a row, alongside the change it caused."""
    usd = state.currency is Currency.USD
    return ScopeMeasures(
        units=state.units,
        acb_cad=state.acb_cad,
        acb_usd=state.acb_usd if usd else ZERO,
        delta_cad=state.acb_cad - before.acb_cad,
        delta_usd=(state.acb_usd - before.acb_usd) if usd else ZERO,
        avg_cad=safe_div(state.acb_cad, state.units),
        avg_usd=safe_div(state.acb_usd, state.units) if usd else ZERO,
        gain_cad=state.gain_cad,
        gain_usd=state.gain_usd if usd else ZERO,
    )


class _Replay:
    """One replay in progress. Owns the accumulators and the diagnostics."""

    def __init__(
        self,
        rows: Sequence[TxnRow],
        fx: FxRates,
        cfg: ReplayConfig,
    ) -> None:
        self.rows = rows
        self.fx = fx
        self.cfg = cfg
        self.pools = _Pools()
        self.result = ReplayResult()
        self._seen: set[tuple[WarningCode, int | None, str | None]] = set()
        pairs, unpaired = pair_transfers(rows)
        self.pair_of: dict[int, int] = {}
        for pair in pairs:
            self.pair_of[pair.out_leg.txn_id] = pair.pair_id
            self.pair_of[pair.in_leg.txn_id] = pair.pair_id
        self.unpaired = {row.txn_id for row in unpaired}
        # Cost base carried by an out leg, waiting for its in leg to claim it.
        self.carried: dict[tuple[int, Scope], tuple[Decimal, Decimal]] = {}
        # Track accounts already reported negative, so only the first crossing shows.
        self.reported_negative: set[tuple[str, Currency]] = set()

    def run(self) -> ReplayResult:
        """Walk the ledger and return everything it produced."""
        self._warn_unknown_accounts()
        self._acb_walk()
        self._cash_walk()
        self._final_position_check()
        self._superficial_loss_check()
        self._attach_flags()
        return self.result

    # -- DIAGNOSTICS ------------------------------------------------------

    def warn(
        self,
        code: WarningCode,
        row: TxnRow | None = None,
        scope: Scope | None = None,
        pool: str | None = None,
        detail: str = "",
    ) -> None:
        """Record a diagnostic once per (code, row, pool)."""
        txn_id = row.txn_id if row is not None else None
        key = (code, txn_id, pool)
        if key in self._seen:
            return
        self._seen.add(key)
        self.result.warnings.append(
            ReplayWarning(
                code=code,
                txn_id=txn_id,
                scope=scope,
                pool=pool,
                detail=detail,
            ),
        )

    def _attach_flags(self) -> None:
        """Tag each master row with every code raised against it.

        Runs last, because the cash walk and the end-of-replay checks raise
        codes long after the row that carries them was emitted.
        """
        self.result.rows = [
            replace(computed, flags=self.result.codes_for(computed.row.txn_id))
            for computed in self.result.rows
        ]

    def _warn_unknown_accounts(self) -> None:
        """Report accounts whose tax type could not be inferred."""
        for account, acct_type in sorted(self.cfg.account_types.items()):
            if acct_type is AccountType.UNKNOWN:
                self.warn(
                    WarningCode.UNKNOWN_ACCOUNT_TYPE,
                    scope=Scope.ACCOUNT,
                    pool=account,
                    detail=f"Could not infer a tax type for account '{account}'",
                )

    # -- CURRENCY SHORTCUTS -----------------------------------------------

    def to_cad(self, amount: Decimal, row: TxnRow) -> Decimal:
        """Convert a row amount into CAD at that row's settle-date rate."""
        return self.fx.to_cad(amount, row.fx_date, row.currency).value

    def rate_for(self, row: TxnRow) -> tuple[Decimal | None, str | None]:
        """Return the USDCAD rate applied to a row, or None for a CAD row."""
        if row.currency is Currency.CAD:
            return None, None
        conversion = self.fx.to_cad(ONE, row.fx_date, row.currency)
        return conversion.rate, conversion.rate_date

    # -- THE COST-BASE WALK -----------------------------------------------

    def _acb_walk(self) -> None:
        """Apply every row to all three pools, in trade-date order."""
        ordered = sorted(
            self.rows,
            key=lambda row: (
                row.txn_date,
                _INTRADAY_ORDER.get(row.action, _INTRADAY_DEFAULT),
                row.txn_id,
            ),
        )
        for row in ordered:
            self.result.rows.append(self._apply(row))

    def _apply(self, row: TxnRow) -> ComputedRow:
        """Apply one row to all three pools and emit a master row."""
        acct_type = self.cfg.account_types.get(row.account, AccountType.UNKNOWN)
        symbol = (
            self.cfg.symbols.canonical(row.ticker, row.txn_date) if row.ticker else None
        )
        rate, rate_date = self.rate_for(row)
        self._row_checks(row)

        measures: dict[Scope, ScopeMeasures] = {}
        for scope in Scope:
            if symbol is None:
                measures[scope] = ScopeMeasures()
                continue
            pool = _pool_key(scope, row, acct_type)
            state = self.pools.state(scope, pool, symbol)
            before = replace(state)
            self._apply_to_pool(row, scope, pool, symbol, state)
            measures[scope] = _snapshot(state, before)

        proceeds_cad, proceeds_usd = self._proceeds(row)
        dividend_cad, dividend_usd = self._dividend(row)

        return ComputedRow(
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
            acct=measures[Scope.ACCOUNT],
            type=measures[Scope.TYPE],
            folio=measures[Scope.FOLIO],
        )

    def _row_checks(self, row: TxnRow) -> None:
        """Diagnostics that read a row on its own, with no pool state."""
        if (
            row.action in _TRADE_ACTIONS
            and row.settle_date
            and row.txn_date
            and row.settle_date < row.txn_date
        ):
            self.warn(
                WarningCode.SETTLE_BEFORE_TRADE,
                row,
                detail=f"Settles {row.settle_date}, traded {row.txn_date}",
            )
        if row.action is Action.FXT:
            self._check_fxt(row)
        elif row.action in (Action.TFR_IN, Action.TFR_OUT):
            # Checked here rather than in the pool walk, to also capture cash transfers
            # that carry no ticker.
            if row.txn_id in self.unpaired:
                self.warn(
                    WarningCode.TRANSFER_UNPAIRED,
                    row,
                    detail=f"{row.action} has no matching counterpart leg",
                )
        elif row.action in _TRADE_ACTIONS and row.fee != ZERO:
            excluded_fits, included_fits = _convention_fits(row, self._fee_sign(row))
            if not (excluded_fits or included_fits):
                self.warn(
                    WarningCode.AMBIGUOUS_FEE_CONVENTION,
                    row,
                    detail=(
                        f"Amount {row.amount} matches neither Price*Units nor "
                        f"Price*Units +/- Fee {row.fee}"
                    ),
                )

    def _apply_to_pool(
        self,
        row: TxnRow,
        scope: Scope,
        pool: str,
        symbol: str,
        state: PositionState,
    ) -> None:
        """Dispatch one row against one pool's position."""
        if state.currency is None:
            state.currency = row.currency

        # Read before dispatch: the row that closes a position is itself a day
        # the pool still held it, right up to the moment it sold.
        held_before = state.units > ZERO

        if row.action is Action.BUY:
            self._apply_buy(row, state)
        elif row.action is Action.SELL:
            self._apply_sell(row, scope, pool, state)
        elif row.action is Action.ROC:
            self._apply_roc(row, scope, pool, state)
        elif row.action is Action.SPLIT:
            self._apply_split(row, scope, pool, symbol, state)
        elif row.action in (Action.TFR_OUT, Action.TFR_IN):
            self._apply_transfer(row, scope, state)
        elif (
            row.action is Action.DIVIDEND
            and self._income_is_orphaned(row, state)
            and scope is Scope.ACCOUNT
        ):
            # Account grain only: the pooled grains have no position either, and
            # three copies of one finding is noise, not extra information.
            self.warn(
                WarningCode.INCOME_WITHOUT_POSITION,
                row,
                scope=scope,
                pool=pool,
                detail=f"Dividend on {symbol} with no position in {pool}",
            )

        # Recorded after the checks above, so a distribution is judged against
        # the position as it stood before this row.
        if held_before or state.units > ZERO:
            state.last_held = row.txn_date

    def _income_is_orphaned(self, row: TxnRow, state: PositionState) -> bool:
        """Whether income arrived against a pool that has no claim to it."""
        if state.units != ZERO:
            return False
        if state.last_held is None:
            return True
        cutoff = date.fromisoformat(row.txn_date) - timedelta(days=_INCOME_TAIL_DAYS)
        return state.last_held < cutoff.isoformat()

    def _apply_buy(self, row: TxnRow, state: PositionState) -> None:
        """Add units and the full cost, commission included."""
        trade: TradeCash = self._trade(row)
        state.units += abs(row.units)
        state.acb_cad += self.to_cad(trade.cost, row)
        if row.currency is Currency.USD:
            state.acb_usd += trade.cost

    def _apply_sell(
        self,
        row: TxnRow,
        scope: Scope,
        pool: str,
        state: PositionState,
    ) -> None:
        """Remove a proportional slice of the cost base and realize the gain.

        The CAD cost base comes off *proportionally*, not by converting the
        removed native amount. Proceeds, by contrast, are converted at the
        settle-date rate. The gap between the two is the FX component of the
        capital gain, which is exactly what CRA taxes.
        """
        trade: TradeCash = self._trade(row)
        sold = abs(row.units)
        held = state.units

        if held <= ZERO or sold > held:
            self.warn(
                WarningCode.OVERSELL,
                row,
                scope=scope,
                pool=pool,
                detail=f"Sold {sold} against {held} held in {pool}",
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
        state.gain_cad += self.to_cad(trade.proceeds, row) - removed_cad
        if row.currency is Currency.USD:
            state.gain_usd += trade.proceeds - removed_usd

        self._snap_dust(state)

    def _apply_roc(
        self,
        row: TxnRow,
        scope: Scope,
        pool: str,
        state: PositionState,
    ) -> None:
        """Reduce the cost base by a return of capital. No cash moves."""
        native = abs(row.amount)
        roc_cad = self.to_cad(native, row)

        # * Max ACB we can remove is the current ACB itself, but not negative.
        applied_cad = min(roc_cad, max(state.acb_cad, ZERO))
        state.acb_cad -= applied_cad
        excess_cad = roc_cad - applied_cad
        state.gain_cad += excess_cad

        if row.currency is Currency.USD:
            applied_usd = min(native, max(state.acb_usd, ZERO))
            state.acb_usd -= applied_usd
            state.gain_usd += native - applied_usd

        if excess_cad > ZERO:
            self.warn(
                WarningCode.ROC_EXCEEDS_ACB,
                row,
                scope=scope,
                pool=pool,
                detail=f"{excess_cad} of return of capital exceeds cost base in {pool}",
            )
        if self._income_is_orphaned(row, state) and scope is Scope.ACCOUNT:
            self.warn(
                WarningCode.INCOME_WITHOUT_POSITION,
                row,
                scope=scope,
                pool=pool,
                detail=f"Return of capital with no position in {pool}",
            )

    def _apply_split(
        self,
        row: TxnRow,
        scope: Scope,
        pool: str,
        symbol: str,
        state: PositionState,
    ) -> None:
        """Scale a position by the split ratio. The total cost base is untouched.

        `Price` is shares before and `Units` shares after, so a 1:10 split
        multiplies units by ten and divides average cost by ten while the total
        stays byte-identical. `Amount` on a SPLIT row is junk.
        """
        if row.price <= ZERO or row.units <= ZERO:
            # Sign rules keep these out of the folio; skip rather than divide.
            logger.debug("Skipping SPLIT %d: ratio is not positive", row.txn_id)
            return

        ratio = row.units / row.price
        if not self.pools.claim_split(scope, (pool, symbol, row.txn_date, str(ratio))):
            if scope is Scope.ACCOUNT:
                self.warn(
                    WarningCode.DUPLICATE_SPLIT,
                    row,
                    scope=scope,
                    pool=pool,
                    detail=(
                        f"{symbol} already split {ratio} on {row.txn_date} in {pool}"
                    ),
                )
            return

        if state.units == ZERO:
            if scope is Scope.ACCOUNT:
                self.warn(
                    WarningCode.SPLIT_WITHOUT_POSITION,
                    row,
                    scope=scope,
                    pool=pool,
                    detail=f"Split of {symbol} with no position in {pool}",
                )
            return

        state.units *= ratio

    def _apply_transfer(
        self,
        row: TxnRow,
        scope: Scope,
        state: PositionState,
    ) -> None:
        """Move units and cost base between pools without realizing a gain."""
        pair_id = self.pair_of.get(row.txn_id)
        if pair_id is None:
            # Already reported by `_row_checks`; units still have to move, but
            # with no counterpart there is nowhere for the cost base to go.
            self._move_units_only(row, state)
            return

        if row.action is Action.TFR_OUT:
            self._transfer_out(row, scope, state, pair_id)
        else:
            self._transfer_in(row, scope, state, pair_id)

    def _move_units_only(self, row: TxnRow, state: PositionState) -> None:
        """Apply an unpaired leg: units move, the cost base cannot follow them."""
        if not row.is_position_transfer:
            return
        if row.action is Action.TFR_OUT:
            state.units -= abs(row.units)
        else:
            state.units += abs(row.units)
        self._snap_dust(state)

    def _transfer_out(
        self,
        row: TxnRow,
        scope: Scope,
        state: PositionState,
        pair_id: int,
    ) -> None:
        """Remove units and their share of the cost base, holding it for the in leg."""
        if not row.is_position_transfer:
            self.carried[(pair_id, scope)] = (ZERO, ZERO)
            return

        moved = abs(row.units)
        held = state.units
        fraction = ONE if held <= ZERO or moved > held else safe_div(moved, held)
        moved_cad = state.acb_cad * fraction
        moved_native = state.acb_usd * fraction

        state.units -= moved
        state.acb_cad -= moved_cad
        state.acb_usd -= moved_native
        self._snap_dust(state)
        self.carried[(pair_id, scope)] = (moved_cad, moved_native)

    def _transfer_in(
        self,
        row: TxnRow,
        scope: Scope,
        state: PositionState,
        pair_id: int,
    ) -> None:
        """Add units and the cost base the out leg handed over."""
        if not row.is_position_transfer:
            return

        moved_cad, moved_native = self.carried.get((pair_id, scope), (ZERO, ZERO))
        state.units += abs(row.units)
        state.acb_cad += moved_cad

        if row.currency is Currency.USD:
            # Same currency on both legs carries the native figure intact; a
            # currency journal has none to carry, so derive it from CAD.
            state.acb_usd += (
                moved_native
                if moved_native != ZERO
                else self.fx.from_cad(moved_cad, row.fx_date, row.currency).value
            )

    def _snap_dust(self, state: PositionState) -> None:
        """Snap a residual position to zero, rolling leftover cost into gain."""
        if state.units != ZERO and abs(state.units) < DUST_UNITS:
            state.gain_cad -= state.acb_cad
            state.gain_usd -= state.acb_usd
            state.units = ZERO
            state.acb_cad = ZERO
            state.acb_usd = ZERO

    def _convention(self, row: TxnRow) -> FeeConvention:
        """Return the fee convention resolved for a row's account."""
        return self.cfg.fee_conventions.get(row.account, FeeConvention.EXCLUDED)

    def _fee_sign(self, row: TxnRow) -> Decimal:
        """Return the multiplier turning a row's stored fee into a commission."""
        return self.cfg.fee_signs.get(row.account, ONE)

    def _trade(self, row: TxnRow) -> TradeCash:
        """Resolve one trade's cost, proceeds and commission."""
        return resolve_trade_cash(row, self._convention(row), self._fee_sign(row))

    def _check_fxt(self, row: TxnRow) -> None:
        """Reconcile a single fx txn conversion against itself."""
        if ZERO in (row.price, row.units):
            return
        implied = abs(row.units * row.price)
        if abs(abs(row.amount) - implied) > FXT_TOLERANCE:
            self.warn(
                WarningCode.FXT_AMOUNT_INCONSISTENT,
                row,
                detail=f"Amount {row.amount} against Units*Price {implied}",
            )

    def _proceeds(self, row: TxnRow) -> tuple[Decimal | None, Decimal | None]:
        """Sale proceeds, reported apart from the gain on Schedule 3."""
        if row.action is not Action.SELL:
            return None, None
        trade = self._trade(row)
        native = trade.proceeds if row.currency is Currency.USD else None
        return self.to_cad(trade.proceeds, row), native

    def _dividend(self, row: TxnRow) -> tuple[Decimal | None, Decimal | None]:
        """Dividend income, converted because it is reported in CAD.

        Amounts are never coerced positive: IBKR issues reversals as a negative
        dividend paired with a corrected positive one.
        """
        if row.action is not Action.DIVIDEND:
            return None, None
        native = row.amount if row.currency is Currency.USD else None
        return self.to_cad(row.amount, row), native

    # -- THE CASH WALK ----------------------------------------------------

    def _cash_walk(self) -> None:
        """Accumulate cash per pool and currency, in settle-date order."""
        ordered = sorted(self.rows, key=lambda row: (row.settle_date, row.txn_id))
        pending: list[TxnRow] = []
        current_date = ""
        for row in ordered:
            if row.settle_date != current_date and pending:
                self._check_negative_cash(pending, current_date)
                pending = []
            current_date = row.settle_date
            self._apply_cash(row)
            pending.append(row)
        if pending:
            self._check_negative_cash(pending, current_date)

        self._roll_up_realized_gain()

    def _cash(self, scope: Scope, pool: str, currency: Currency) -> CashState:
        """Return the cash state for one pool and currency, creating it if new."""
        key: CashKey = (scope, pool, currency)
        return self.result.cash.setdefault(key, CashState())

    def _credit(  # noqa: PLR0913 - one call site per action, all named
        self,
        row: TxnRow,
        currency: Currency,
        *,
        cash: Decimal = ZERO,
        contributions: Decimal = ZERO,
        withdrawals: Decimal = ZERO,
        dividends: Decimal = ZERO,
        fees: Decimal = ZERO,
    ) -> None:
        """Apply one cash movement to all three pool grains at once."""
        acct_type = self.cfg.account_types.get(row.account, AccountType.UNKNOWN)
        for scope in Scope:
            state = self._cash(scope, _pool_key(scope, row, acct_type), currency)
            state.cash += cash
            state.contributions += contributions
            state.withdrawals += withdrawals
            state.dividends += dividends
            state.fees += fees

    def _apply_cash(self, row: TxnRow) -> None:
        """Book one row's effect on cash.

        `ROC` and `SPLIT` are deliberately excluded: a return of capital
        reclassifies a distribution that was already paid as a dividend, so
        counting it as cash double-counts the money, and a split moves no money.
        """
        if row.action is Action.BUY:
            trade: TradeCash = self._trade(row)
            self._credit(row, row.currency, cash=-trade.cost, fees=trade.commission)
        elif row.action is Action.SELL:
            trade: TradeCash = self._trade(row)
            self._credit(row, row.currency, cash=trade.proceeds, fees=trade.commission)
        elif row.action is Action.DIVIDEND:
            self._credit(row, row.currency, cash=row.amount, dividends=row.amount)
        elif row.action is Action.FCH:
            # When row amount negative, the charged fee (abs) is recorded.
            charged = -row.amount if row.amount < ZERO else ZERO
            self._credit(row, row.currency, cash=row.amount, fees=charged)
        elif row.action is Action.CONTRIBUTION:
            self._credit(
                row,
                row.currency,
                cash=row.amount,
                contributions=abs(row.amount),
            )
        elif row.action is Action.WITHDRAWAL:
            self._credit(
                row,
                row.currency,
                cash=row.amount,
                withdrawals=abs(row.amount),
            )
        elif row.action is Action.FXT:
            self._apply_fxt_cash(row)
        elif row.action in (Action.TFR_IN, Action.TFR_OUT) and not (
            row.is_position_transfer
        ):
            # Transfers are not contributions: they consume no contribution room.
            self._credit(row, row.currency, cash=row.amount)

    def _apply_fxt_cash(self, row: TxnRow) -> None:
        """Book a currency conversion, in whichever of its two shapes it is.

        A single-leg (IBKR) row carries the CAD delta in `Amount`, the USD delta
        in `Units` and the rate in `Price`. A two-leg (QuestTrade) row has no
        `Price` or `Units`, and its `Amount` applies to its own currency.

        The IBKR FX commission is a separate debit charged in USD. `Units` is
        the gross USD received, so the fee comes off on top of it, whatever the
        row's own `$` says.
        """
        commission = row.fee * self._fee_sign(row)
        if ZERO in (row.price, row.units):
            self._credit(
                row,
                row.currency,
                cash=row.amount - commission,
                fees=commission,
            )
            return

        self._credit(row, Currency.CAD, cash=row.amount)
        self._credit(
            row,
            Currency.USD,
            cash=row.units - commission,
            fees=commission,
        )

    def _roll_up_realized_gain(self) -> None:
        """Carry realized gains from the position pools onto the cash states.

        Gains land in the holding's own currency, matching every other figure a
        `CashState` carries. The CAD-converted view lives on the master frame.
        """
        for scope in Scope:
            for (pool, _symbol), state in self.pools.all_states(scope):
                if state.currency is Currency.USD:
                    bucket = self._cash(scope, pool, Currency.USD)
                    bucket.realized_gain += state.gain_usd
                else:
                    bucket = self._cash(scope, pool, Currency.CAD)
                    bucket.realized_gain += state.gain_cad

    def _check_negative_cash(self, rows: Sequence[TxnRow], on: str) -> None:
        """Report the first date an account's cash crosses below zero.

        Negative cash needs no external balance to detect and catches a missing
        transaction immediately. Only account pools are judged: a pooled type or
        portfolio balance nets accounts against each another and says nothing
        about a missing row.

        **Judged to the cent**, which is the unit cash is denominated in and the
        precision every other reading of a balance already uses.
        """
        for account in sorted({row.account for row in rows}):
            acct_type = self.cfg.account_types.get(account, AccountType.UNKNOWN)
            if acct_type is AccountType.MARGIN:
                continue
            for currency in (Currency.CAD, Currency.USD):
                if (account, currency) in self.reported_negative:
                    continue
                state = self.result.cash.get((Scope.ACCOUNT, account, currency))
                if state is None or q2(state.cash) >= ZERO:
                    continue
                self.reported_negative.add((account, currency))
                first = next(row for row in rows if row.account == account)
                self.warn(
                    WarningCode.CASH_NEGATIVE,
                    first,
                    scope=Scope.ACCOUNT,
                    pool=f"{account}:{currency}",
                    detail=f"{account} {currency} cash is {state.cash} as of {on}",
                )

    # -- FINAL CHECKS -----------------------------------------------------

    def _final_position_check(self) -> None:
        """Report any pool that ends below zero units."""
        for scope in Scope:
            for (pool, symbol), state in self.pools.all_states(scope):
                if state.units < -DUST_UNITS:
                    self.warn(
                        WarningCode.NEGATIVE_FINAL_POSITION,
                        scope=scope,
                        pool=f"{pool}:{symbol}",
                        detail=f"{symbol} ends at {state.units} units in {pool}",
                    )

    def _superficial_loss_check(self) -> None:
        """Flag a realized loss with a buy of the same symbol in the CRA window.

        A warning flag only, deliberately: cost base and gain are NOT adjusted
        for it, by design, so units/ACB/avg cost keep matching a broker's own
        figures 1:1.

        The CRA superficial-loss window spans from 30 days before
        the sale to 30 days after it (inclusive of the sale date). Restricted
        to taxable accounts, the only place the rule has any consequence, but
        the triggering buy may be in any account.
        """
        buys: dict[str, list[date]] = {}
        for computed in self.result.rows:
            if computed.row.action is Action.BUY and computed.symbol:
                buys.setdefault(computed.symbol, []).append(
                    date.fromisoformat(computed.row.txn_date),
                )

        for computed in self.result.rows:
            row = computed.row
            if row.action is not Action.SELL or not computed.symbol:
                continue
            acct_type = self.cfg.account_types.get(row.account, AccountType.UNKNOWN)
            if acct_type not in TAXABLE_ACCOUNT_TYPES or _sale_gain(computed) >= ZERO:
                continue
            sold_on = date.fromisoformat(row.txn_date)
            window_start = sold_on - timedelta(days=SUPERFICIAL_LOSS_DAYS)
            window_end = sold_on + timedelta(days=SUPERFICIAL_LOSS_DAYS)
            repurchased = buys.get(computed.symbol, [])
            if any(window_start <= when <= window_end for when in repurchased):
                self.warn(
                    WarningCode.SUPERFICIAL_LOSS_SUSPECT,
                    row,
                    detail=(
                        f"Loss on {computed.symbol} with a buy within "
                        f"{SUPERFICIAL_LOSS_DAYS} days"
                    ),
                )


def _sale_gain(computed: ComputedRow) -> Decimal:
    """Compute the gain one SELL realized in its own account, in CAD."""
    # cost base removed is negative, so negated delta_cad is the positive acb removed
    removed = -computed.acct.delta_cad
    # gains = total proceeds - cost base removed
    return (computed.proceeds_cad or ZERO) - removed


def replay(rows: Sequence[TxnRow], fx: FxRates, cfg: ReplayConfig) -> ReplayResult:
    """Replay the ledger into units, cost base, gains and cash.

    Args:
        rows: Every transaction to replay (TxnRows)
        fx: FX rates for currency conversion.
        cfg: Replay configuration: Per-account types, fee conventions, symbol resolution

    Returns:
        One master row per transaction, the diagnostics raised, and running cash
        per pool grain, pool and currency.
    """
    return _Replay(rows, fx, cfg).run()
