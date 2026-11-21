"""Read the cash and income totals a replay already accumulated.

A transfer still moves *net deposits* between accounts, just not the whole value
it carries: see `engine.deposits` for the proportional rule that decides how
much, and why neither face value nor cost base is the right answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from domain import (
    ROOM_BEARING_TYPES,
    WEEKDAYS,
    AccountType,
    Action,
    Column,
    Currency,
    Grain,
    Period,
)
from domain.numeric import ZERO, dec, q2
from engine.types import CashState

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from decimal import Decimal

    import pandas as pd

    from engine.accounts import Pool
    from engine.ledger import Ledger

# An RRSP contribution made up to 60 days after a year ends counts toward that
# year's room, which is how CRA's deduction limit statement reports it.
RRSP_WINDOW_DAYS = 60


@dataclass(frozen=True)
class Room:
    """Contribution room for one account type in one year.

    Attributes:
        account_type: The type the limit applies to.
        year: Calendar year.
        used: Contributions recorded against that type this year.
        limit: The configured limit, or None when none is configured.
        withdrawn: Withdrawals from that type this year. They never give room
            back in the same year, so `remaining` ignores them.
    """

    account_type: AccountType
    year: int
    used: Decimal
    limit: Decimal | None = None
    withdrawn: Decimal = ZERO

    @property
    def remaining(self) -> Decimal | None:
        """How much room is left, or None when no limit is configured."""
        return None if self.limit is None else self.limit - self.used

    @property
    def over(self) -> bool:
        """Whether contributions have exceeded the configured limit."""
        remaining = self.remaining
        return remaining is not None and remaining < ZERO

    @property
    def used_ratio(self) -> Decimal | None:
        """How much of the limit is used, as a ratio, or None without a usable limit."""
        if self.limit is None or self.limit == ZERO:
            return None
        return self.used / self.limit

    @property
    def full(self) -> bool:
        """Whether contributions meet the configured limit exactly, to the cent."""
        return self.limit is not None and q2(self.used) == q2(self.limit)


@dataclass(frozen=True)
class Flows:
    """Cash and cumulative income for one pool, per currency.

    Attributes:
        pool: The pool these figures were read for.
        account_type: The type this pool is, when it is exactly one.
        cash: Running cash balance.
        contributions: Cumulative `CONTRIBUTION` only.
        withdrawals: Cumulative `WITHDRAWAL` only.
        transfers: Cumulative *deposits* carried by `TFR_IN`/`TFR_OUT`, signed
            (in on a `TFR_IN`, out on a `TFR_OUT`). Not the value moved: a leg
            takes the share of the sending pool's deposits that matches the
            share of the pool it removes, so growth stays behind. See
            `engine.deposits`. This is needed for net deposits.
        transfers_value: What those transfers actually moved, cash at face and
            securities at the cost base they carried.
        dividends: Cumulative dividends, signed, so reversals stay visible.
        fees: Cumulative commissions and charges.
        realized: Cumulative realized gains.
        bought: What buys cost, commission included.
        sold: What sales brought in, after commission.
        trades: How many buys and sells.
        room: Contribution room for the current year, when the pool has a type
            that carries one.
    """

    pool: Pool
    account_type: AccountType | None = None
    cash: dict[Currency, Decimal] = field(default_factory=dict)
    contributions: dict[Currency, Decimal] = field(default_factory=dict)
    withdrawals: dict[Currency, Decimal] = field(default_factory=dict)
    transfers: dict[Currency, Decimal] = field(default_factory=dict)
    transfers_value: dict[Currency, Decimal] = field(default_factory=dict)
    dividends: dict[Currency, Decimal] = field(default_factory=dict)
    fees: dict[Currency, Decimal] = field(default_factory=dict)
    realized: dict[Currency, Decimal] = field(default_factory=dict)
    bought: dict[Currency, Decimal] = field(default_factory=dict)
    sold: dict[Currency, Decimal] = field(default_factory=dict)
    trades: dict[Currency, int] = field(default_factory=dict)
    room: Room | None = None

    @property
    def grain(self) -> Grain:
        """The grain the pool sits at."""
        return self.pool.grain

    @property
    def lines(self) -> tuple[FlowLine, ...]:
        """One line per currency the pool's flows moved, CAD first. Cash is no flow."""
        moved = (
            self.contributions,
            self.withdrawals,
            self.transfers,
            self.bought,
            self.sold,
            self.dividends,
            self.fees,
            self.realized,
            self.trades,
        )
        currencies = sorted(set().union(*moved), key=str)
        return tuple(FlowLine(self, currency) for currency in currencies)

    @property
    def net_deposits(self) -> dict[Currency, Decimal]:
        """How much of the holder's own money this pool still contains.

        Contributions, less withdrawals, plus the deposit parts of transfers carried in
        or out.

        A transfer moves only the *deposit* share of what it carries, worked out
        in `engine.deposits`, so an account that grew and then swept cash to
        another broker keeps the growth and moves the deposits. Both legs of a
        pair move the same figure, and a pair sitting inside one pool is skipped
        entirely, so this reduces to exactly contributions less withdrawals at
        any grain containing both sides: folio-wide it is the money the
        holder ever put in, and per account the money that reached that account.
        """
        currencies = (
            set(self.contributions) | set(self.withdrawals) | set(self.transfers)
        )
        return {
            currency: (
                self.contributions.get(currency, ZERO)
                - self.withdrawals.get(currency, ZERO)
                + self.transfers.get(currency, ZERO)
            )
            for currency in currencies
        }

    @property
    def negative_currencies(self) -> tuple[Currency, ...]:
        """Currencies whose cash balance has gone below zero.

        **Judged at cent precision**, exactly as the replay's own
        `CASH_NEGATIVE` diagnostic judges it. Converting hundreds of rows
        through FX leaves residues of a millionth of a cent that needs to be bypassed.
        """
        return tuple(
            currency for currency, amount in self.cash.items() if q2(amount) < ZERO
        )

    def moved_by_transfers(self, currency: Currency) -> Decimal | None:
        """Report what transfers moved in `currency`, when not the deposits carried.

        A transfer carries only the deposit share of what it moves, so the two
        part ways once the sending pool has grown or shrunk. **Judged at cent
        precision**, so a residue of the proportional split reads as equal.

        Args:
            currency: The currency to compare in.

        Returns:
            The value moved, or None when it matches the deposits carried or
            no transfer moved any.
        """
        moved = self.transfers_value.get(currency)
        if moved is None or q2(moved) == q2(self.transfers.get(currency, ZERO)):
            return None
        return moved

    @property
    def net_deposit_denominator(self) -> Decimal | None:
        """The CAD money the holder put in, as a return denominator.

        **CAD only** `net_deposits` keeps deposits per currency
        and never blends them, because a blended figure hides an FX rate and
        its date. A USD contribution would have to be converted at *some* rate
        to join this sum, and no honest one exists

        **Judged at cent precision**, just like in many other places, as partial
        transfers can leave behind residues causing bad divisions.

        Returns:
            Net CAD deposits, or None when there are none to divide by.
        """
        deposited = self.net_deposits.get(Currency.CAD)
        if deposited is None or q2(deposited) == ZERO:
            return None
        return deposited


@dataclass(frozen=True)
class FlowLine:
    """One currency's flows in one pool: the row a flows table shows.

    Attributes:
        flows: The pool's flows.
        currency: The currency this line reads.
    """

    flows: Flows
    currency: Currency


def build_flows(  # noqa: PLR0913
    ledger: Ledger,
    pool: Pool,
    *,
    contributions: Mapping[AccountType, Mapping[int, Decimal]],
    account_type: AccountType | None = None,
    room: Mapping[AccountType, Mapping[int, Decimal]] | None = None,
    year: int | None = None,
) -> Flows:
    """Collect one pool's cash and income totals out of the ledger.

    Args:
        ledger: The ledger, whose cash carries every pool's flows.
        pool: The pool to read.
        account_type: The type this pool is, when it is exactly one. Drives
            whether a contribution-room row applies at all.
        room: Configured limits by account type and year.
        contributions: Every type's per-year contributions
        year: Calendar year to report room for. Defaults to the latest year
            with contributions, so a folio not yet traded in this year still
            reports something meaningful.

    Returns:
        The pool's figures, per currency.
    """
    states = {
        currency: state
        for (cash_pool, currency), state in ledger.cash.items()
        if cash_pool == pool
    }
    return _read_flows(
        pool,
        states,
        account_type=account_type,
        room=_room(account_type, room or {}, year, contributions),
    )


@dataclass(frozen=True)
class PeriodFlows:
    """One pool's flows within one period.

    Attributes:
        start: The period's first day.
        flows: What settled in it. Cash is left out: a balance is not a flow.
    """

    start: date
    flows: Flows


def flows_by_period(ledger: Ledger, pool: Pool, period: Period) -> list[PeriodFlows]:
    """Break one pool's flows down by the period each row settled in.

    Read off the same replay as `build_flows`, transfers at the deposit share each
    leg moved, so the periods add up to the pool's running totals.

    Args:
        ledger: The ledger.
        pool: The pool to read.
        period: How long each period is.

    Returns:
        One entry per period that saw a flow, newest first.
    """
    by_start = _states_by(ledger, pool, period.start)
    return [
        PeriodFlows(start, _read_flows(pool, states))
        for start, states in sorted(by_start.items(), reverse=True)
    ]


def flow_totals(ledger: Ledger, pool: Pool) -> Flows:
    """Add up one pool's flows over its whole history, as `flows_by_period` reads them.

    Args:
        ledger: The ledger.
        pool: The pool to read.

    Returns:
        The pool's flows, cash left out.
    """
    states = _states_by(ledger, pool, lambda _day: None).get(None, {})
    return _read_flows(pool, states)


def _states_by[K](
    ledger: Ledger,
    pool: Pool,
    key: Callable[[date], K],
) -> dict[K, dict[Currency, CashState]]:
    """Fold one pool's row flows and deposit shares into states, grouped by date key."""
    grouped: dict[K, dict[Currency, CashState]] = {}

    def state_of(settle_date: str, currency: Currency) -> CashState:
        bucket = grouped.setdefault(key(date.fromisoformat(settle_date)), {})
        return bucket.setdefault(currency, CashState())

    for flow in ledger.row_flows:
        if flow.pool(pool.grain) != pool:
            continue
        state = state_of(flow.settle_date, flow.effect.currency)
        state.book(flow.effect)
        state.realized_gain += flow.realized.get(pool.grain, ZERO)
    for share in ledger.deposit_shares:
        if share.pool == pool:
            state_of(share.settle_date, share.currency).transfers += share.amount
    return grouped


def _read_flows(
    pool: Pool,
    states: Mapping[Currency, CashState],
    *,
    account_type: AccountType | None = None,
    room: Room | None = None,
) -> Flows:
    """Read a pool's flows out of its states, one per currency."""
    return Flows(
        pool=pool,
        account_type=account_type,
        cash=_measure(states, "cash"),
        contributions=_measure(states, "contributions"),
        withdrawals=_measure(states, "withdrawals"),
        transfers=_measure(states, "transfers"),
        transfers_value=_measure(states, "transfers_value"),
        dividends=_measure(states, "dividends"),
        fees=_measure(states, "fees"),
        realized=_measure(states, "realized_gain"),
        bought=_measure(states, "bought"),
        sold=_measure(states, "sold"),
        trades=_measure(states, "trades"),
        room=room,
    )


def contributions_by_type_year(
    frame: pd.DataFrame,
) -> dict[AccountType, dict[int, Decimal]]:
    """Total contributions to every account type, by the year they count toward.

    That is the calendar year, except for an RRSP: a contribution made in the
    first 60 days of a year counts toward the year before (see `room_year`).

    Only `CONTRIBUTION` counts. A transfer between two accounts you already own
    consumes no room, however much cash it moves.

    Args:
        frame: The ledger.

    Returns:
        Each account type mapped to its per-year contributions.
    """
    return _by_type_year(frame, Action.CONTRIBUTION)


def withdrawals_by_type_year(
    frame: pd.DataFrame,
) -> dict[AccountType, dict[int, Decimal]]:
    """Total withdrawals from every account type, by calendar year.

    Only `WITHDRAWAL` counts, for the same reason transfers are left out of
    `contributions_by_type_year`.

    Args:
        frame: The ledger.

    Returns:
        Each account type mapped to its per-year withdrawals, as positive sums.
    """
    return _by_type_year(frame, Action.WITHDRAWAL)


def room_history(
    contributions: Mapping[AccountType, Mapping[int, Decimal]],
    withdrawals: Mapping[AccountType, Mapping[int, Decimal]],
    configured: Mapping[AccountType, Mapping[int, Decimal]],
    *,
    through: int,
) -> list[Room]:
    """List every year of contributions the folio has used.

    Args:
        contributions: Every type's per-year contributions.
        withdrawals: Every type's per-year withdrawals.
        configured: Configured limits by account type and year.
        through: The last year to list a limit-only year for, so a limit
            configured ahead of time does not read as room already unused.

    Returns:
        One `Room` per type and year, newest year first, types alphabetical.
    """
    history: list[Room] = []
    for account_type in ROOM_BEARING_TYPES:
        put_in = contributions.get(account_type, {})
        taken_out = withdrawals.get(account_type, {})
        if not put_in and not taken_out:
            continue
        limits = configured.get(account_type, {})
        years = set(put_in) | set(taken_out)
        years |= {year for year in limits if year <= through}
        history.extend(
            Room(
                account_type=account_type,
                year=year,
                used=put_in.get(year, ZERO),
                limit=limits.get(year),
                withdrawn=taken_out.get(year, ZERO),
            )
            for year in years
        )
    history.sort(key=lambda room: (-room.year, str(room.account_type)))
    return history


def _by_type_year(
    frame: pd.DataFrame,
    action: Action,
) -> dict[AccountType, dict[int, Decimal]]:
    """Total one cash action's amounts per account type and calendar year."""
    if frame.empty or str(Column.Txn.ACTION) not in frame.columns:
        return {}

    rows = frame[frame[str(Column.Txn.ACTION)] == str(action)]
    totals: dict[AccountType, dict[int, Decimal]] = {}
    for name, stamp, amount in zip(
        rows[Column.Ledger.ACCT_TYPE].to_numpy(),
        rows[str(Column.Txn.TXN_DATE)].to_numpy(),
        rows[str(Column.Txn.AMOUNT)].to_numpy(),
        strict=True,
    ):
        # The database holds every TxnDate as YYYY-MM-DD.
        day = date.fromisoformat(str(stamp)[:10])
        try:
            account_type = AccountType(str(name))
        except ValueError:
            continue
        # Only putting money in has a deadline that reaches back a year.
        contributing = action is Action.CONTRIBUTION
        year = room_year(account_type, day) if contributing else day.year
        by_year = totals.setdefault(account_type, {})
        by_year[year] = by_year.get(year, ZERO) + abs(dec(amount))
    return totals


def _measure(
    states: Mapping[Currency, CashState],
    name: str,
) -> dict[Currency, Any]:
    """Pull one `CashState` field out for every currency, dropping the zeros."""
    return {
        currency: value
        for currency, state in states.items()
        if (value := getattr(state, name)) != ZERO
    }


def _room(
    account_type: AccountType | None,
    configured: Mapping[AccountType, Mapping[int, Decimal]],
    year: int | None,
    contributions: Mapping[AccountType, Mapping[int, Decimal]],
) -> Room | None:
    """Work out the contribution-room line, or None when it does not apply."""
    if account_type is None or account_type not in ROOM_BEARING_TYPES:
        return None

    by_year = contributions.get(account_type, {})
    resolved = year if year is not None else (max(by_year) if by_year else None)
    if resolved is None:
        return None

    limits = configured.get(account_type, {})
    return Room(
        account_type=account_type,
        year=resolved,
        used=by_year.get(resolved, ZERO),
        limit=limits.get(resolved),
    )


def room_year(account_type: AccountType, day: date) -> int:
    """Name the year a contribution made on `day` uses the room of.

    An RRSP contribution made within 60 days of a year's end counts toward that
    year. When the 60th day falls on a weekend, CRA moves the deadline to the
    Monday after, and so does this. Every other type counts the calendar year.

    Args:
        account_type: The type contributed to.
        day: When the contribution was made.

    Returns:
        The year whose room the contribution uses.
    """
    if account_type is not AccountType.RRSP:
        return day.year
    deadline = date(day.year - 1, 12, 31) + timedelta(days=RRSP_WINDOW_DAYS)
    while deadline.weekday() >= WEEKDAYS:
        deadline += timedelta(days=1)
    return day.year - 1 if day <= deadline else day.year
