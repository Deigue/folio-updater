"""Read the cash and income totals a replay already accumulated.

A transfer still moves *net deposits* between accounts, just not the whole value
it carries: see `engine.deposits` for the proportional rule that decides how
much, and why neither face value nor cost base is the right answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING

from domain import AccountType, Action, Column, Currency, Scope
from domain.numeric import ZERO, dec, q2

if TYPE_CHECKING:
    from collections.abc import Mapping
    from decimal import Decimal

    import pandas as pd

    from engine.types import CashState, ReplayResult

# The pool key the replay credits portfolio-grain movements to.
FOLIO_POOL = "FOLIO"

# An RRSP contribution made up to 60 days after a year ends counts toward that
# year's room, which is how CRA's deduction limit statement reports it.
RRSP_WINDOW_DAYS = 60
_SATURDAY = 5

# Account types that carry a CRA contribution limit worth reporting against.
ROOM_BEARING_TYPES = frozenset(
    {
        AccountType.TFSA,
        AccountType.RRSP,
        AccountType.RESP,
        AccountType.FHSA,
    },
)


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
        scope: The grain these figures were read at.
        pool: The pool key, empty at portfolio grain.
        label: How the pool reads in a heading.
        account_type: The type this pool is, when it is exactly one.
        cash: Running cash balance.
        contributions: Cumulative `CONTRIBUTION` only.
        withdrawals: Cumulative `WITHDRAWAL` only.
        transfers: Cumulative *deposits* carried by `TFR_IN`/`TFR_OUT`, signed
            (in on a `TFR_IN`, out on a `TFR_OUT`). Not the value moved: a leg
            takes the share of the sending pool's deposits that matches the
            share of the pool it removes, so growth stays behind. See
            `engine.deposits`. This is needed for Net Deposited.
        transfers_value: What those transfers actually moved, cash at face and
            securities at the cost base they carried.
        dividends: Cumulative dividends, signed, so reversals stay visible.
        fees: Cumulative commissions and charges.
        realized: Cumulative realized gains.
        room: Contribution room for the current year, when the pool has a type
            that carries one.
    """

    scope: Scope
    pool: str
    label: str
    account_type: AccountType | None = None
    cash: dict[Currency, Decimal] = field(default_factory=dict)
    contributions: dict[Currency, Decimal] = field(default_factory=dict)
    withdrawals: dict[Currency, Decimal] = field(default_factory=dict)
    transfers: dict[Currency, Decimal] = field(default_factory=dict)
    transfers_value: dict[Currency, Decimal] = field(default_factory=dict)
    dividends: dict[Currency, Decimal] = field(default_factory=dict)
    fees: dict[Currency, Decimal] = field(default_factory=dict)
    realized: dict[Currency, Decimal] = field(default_factory=dict)
    room: Room | None = None

    @property
    def net_deposited(self) -> dict[Currency, Decimal]:
        """How much of the holder's own money this pool still contains.

        Contributions, less withdrawals, plus the deposit parts of transfers carried in
        or out.

        A transfer moves only the *deposit* share of what it carries, worked out
        in `engine.deposits`, so an account that grew and then swept cash to
        another broker keeps the growth and moves the deposits. Both legs of a
        pair move the same figure, and a pair sitting inside one pool is skipped
        entirely, so this reduces to exactly contributions less withdrawals at
        any grain containing both sides: portfolio-wide it is the money the
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

    @property
    def net_deposit_denominator(self) -> Decimal | None:
        """The CAD money the holder put in, as a return denominator.

        **CAD only** `net_deposited` keeps deposits per currency
        and never blends them, because a blended figure hides an FX rate and
        its date. A USD contribution would have to be converted at *some* rate
        to join this sum, and no honest one exists

        **Judged at cent precision**, just like in many other places, as partial
        transfers can leave behind residues causing bad divisions.

        Returns:
            Net CAD deposits, or None when there are none to divide by.
        """
        deposited = self.net_deposited.get(Currency.CAD)
        if deposited is None or q2(deposited) == ZERO:
            return None
        return deposited


def build_flows(  # noqa: PLR0913
    result: ReplayResult,
    *,
    scope: Scope,
    pool: str | None,
    label: str,
    contributions: Mapping[AccountType, Mapping[int, Decimal]],
    account_type: AccountType | None = None,
    room: Mapping[AccountType, Mapping[int, Decimal]] | None = None,
    year: int | None = None,
) -> Flows:
    """Collect one pool's cash and income totals out of a replay.

    Args:
        result: A completed replay. Its `cash` totals survive a cache round
            trip intact, which is why they are read from here.
        scope: The pool grain to read at.
        pool: The account name or account type. Ignored at portfolio grain,
            where the replay credits the literal `FOLIO` key.
        label: How the pool should read in a heading.
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
    key = FOLIO_POOL if scope is Scope.FOLIO else (pool or "")
    states = {
        currency: state
        for (grain, name, currency), state in result.cash.items()
        if grain is scope and name == key
    }

    return Flows(
        scope=scope,
        pool=key if scope is not Scope.FOLIO else "",
        label=label,
        account_type=account_type,
        cash=_measure(states, "cash"),
        contributions=_measure(states, "contributions"),
        withdrawals=_measure(states, "withdrawals"),
        transfers=_measure(states, "transfers"),
        transfers_value=_measure(states, "transfers_value"),
        dividends=_measure(states, "dividends"),
        fees=_measure(states, "fees"),
        realized=_measure(states, "realized_gain"),
        room=_room(account_type, room or {}, year, contributions),
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
        frame: The master frame.

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
        frame: The master frame.

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
        rows["AcctType"].to_numpy(),
        rows[str(Column.Txn.TXN_DATE)].to_numpy(),
        rows[str(Column.Txn.AMOUNT)].to_numpy(),
        strict=True,
    ):
        day = _date_of(str(stamp))
        if day is None:
            continue
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
) -> dict[Currency, Decimal]:
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
    while deadline.weekday() >= _SATURDAY:
        deadline += timedelta(days=1)
    return day.year - 1 if day <= deadline else day.year


def _date_of(text: str) -> date | None:
    """Read a `YYYY-MM-DD` date, or None when it is not one."""
    try:
        return date.fromisoformat(text[:10])
    except (TypeError, ValueError):
        return None
