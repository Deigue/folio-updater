"""The demo folio: two realistic years of activity built from real market history.

The market history is `datagen.history_data`: real month-end closes, dividends
and splits for every symbol the demo trades, which keeps generation offline and
deterministic. `datagen.history` refreshes it from Yahoo Finance.

The price history stores what Yahoo serves: closes and dividends adjusted for every
split since, as if today's unit count had always applied. A price or a
dividend on a given day is un-adjusted when it is read, so a trade before a
split books the price the market actually showed that day.
"""

from __future__ import annotations

import calendar
import heapq
import inspect
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal
from functools import cache
from itertools import count
from typing import TYPE_CHECKING, Any

import pandas as pd

from app import get_config, initialize_app
from config import write_missing_setting
from datagen.history_data import CLOSES, DIVIDENDS, FIRST_MONTH, SPLITS
from db import (
    backup_folio,
    create_txns_table,
    drop_table,
    get_connection,
    get_distinct_set,
    get_row_count,
    insert_or_replace,
)
from db.helpers import generate_keys
from domain import (
    TAXABLE_ACCOUNT_TYPES,
    TORONTO_TZ,
    TXN_ESSENTIALS,
    AccountType,
    Action,
    Column,
    Currency,
    Table,
)
from domain.numeric import dec
from engine.flows import room_year
from engine.replay import SUPERFICIAL_LOSS_DAYS
from engine.settlement import BUSINESS_DAY_SETTLE_ACTIONS, settlement_calculator
from exporters import ParquetExporter
from importers import insert_transactions
from services import ForexService
from term import console_warning

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

START = date(2024, 10, 1)
END = date(2026, 9, 30)

# Folio symbols the demo trades, spelled the way the folio stores them.
SYMBOLS: tuple[str, ...] = (
    # US blue chips
    "AAPL",
    "MSFT",
    "NVDA",
    "META",
    "COST",
    "KO",
    "JNJ",
    "NFLX",
    "XYZ",
    "INTC",
    "O",
    # US ETFs
    "SPY",
    "SCHD",
    "QQQ",
    # Canadian
    "RY.TO",
    "TD.TO",
    "ENB.TO",
    "CNR.TO",
    "XEQT.TO",
    "VFV.TO",
    "REI.UN.TO",
    "DLR.TO",
    "DLR.U.TO",
)
# The USD/CAD rate, under a name no security uses.
USDCAD = "USDCAD"
# Toronto-listed US dollar lines, and the Canadian dollar line each mirrors.
# Yahoo quotes DLR-U.TO live but serves almost no history for it; since both
# lines hold the same US dollars, the price history falls back to the CAD line's
# closes converted at the month's rate whenever Yahoo's history is short.
USD_LINES: dict[str, str] = {"DLR.U.TO": "DLR.TO"}
# Real renames: the old symbol, the new one, and the first day under the new
# name. Yahoo files the whole history under the new symbol, so the price history
# holds it there.
RENAMES: tuple[tuple[str, str, date], ...] = (("SQ", "XYZ", date(2025, 1, 21)),)

# The CRA's yearly limits. The RRSP figure is the dollar ceiling: a real limit
# is the lesser of it and 18% of the previous year's income.
CRA_ROOM: dict[str, dict[int, int]] = {
    "TFSA": {2024: 7000, 2025: 7000, 2026: 7000},
    "RRSP": {2024: 31560, 2025: 32490, 2026: 33810},
}

# Every demo account is named MOCK-something, or MOCK2- for the second broker.
_DEMO_ACCOUNT = re.compile(r"^MOCK\d*-")

_CENT = Decimal("0.01")
_RATE_PLACES = Decimal("0.0001")


class DemoScenarioError(ValueError):
    """The demo's own data cannot produce a valid folio."""


# -- PRICE HISTORY READING ----------------------------------------------------


# The first day the price history covers.
FIRST_DAY = date.fromisoformat(f"{FIRST_MONTH}-01")


def month_index(on: date) -> int:
    """Count the months from the price history's first month to the month of `on`."""
    return (on.year - FIRST_DAY.year) * 12 + on.month - FIRST_DAY.month


def _parse_events(text: str) -> list[tuple[date, Decimal]]:
    """Read a `YYYY-MM-DD:value` list from the price history."""
    events: list[tuple[date, Decimal]] = []
    for token in text.split():
        day, _, value = token.partition(":")
        events.append((date.fromisoformat(day), dec(value)))
    return events


@cache
def _closes() -> dict[str, list[Decimal]]:
    """Month-end closes per symbol, oldest first, from `FIRST_MONTH` on."""
    return {symbol: [dec(v) for v in text.split()] for symbol, text in CLOSES.items()}


@cache
def _splits() -> dict[str, list[tuple[date, Decimal]]]:
    """Split dates and ratios (new units per old unit) per symbol."""
    return {symbol: _parse_events(text) for symbol, text in SPLITS.items()}


def trading_symbol(symbol: str, on: date) -> str:
    """Name the symbol a security traded under on a given day.

    Args:
        symbol: Any name the security has had.
        on: The day in question.

    Returns:
        The old name before its rename took effect, the new one from then on.
    """
    for old, new, effective in RENAMES:
        if symbol in (old, new):
            return old if on < effective else new
    return symbol


def split_factor(symbol: str, on: date) -> Decimal:
    """How many of today's units one unit held on `on` has become.

    Args:
        symbol: Folio symbol.
        on: The day a price or a holding is measured.

    Returns:
        The product of every split ratio dated after `on`, 1 when there is none.
    """
    factor = Decimal(1)
    for day, ratio in _splits().get(symbol, []):
        if day > on:
            factor *= ratio
    return factor


def splits(symbol: str) -> list[tuple[date, Decimal]]:
    """Every split of a symbol in the price history, as (date, new per old)."""
    return list(_splits().get(symbol, []))


def price_on(symbol: str, on: date) -> Decimal:
    """Price a symbol on a given day, as the market showed it.

    Interpolated between the surrounding month-end closes by how far into the
    month `on` falls, then un-adjusted for any split after it.

    Args:
        symbol: Folio symbol, or `USDCAD` for the FX rate.
        on: Any day the price history covers.

    Returns:
        The price to the cent, or the rate to four places.

    Raises:
        DemoScenarioError: If the price history does not cover the symbol or day.
    """
    closes = _closes().get(symbol)
    index = month_index(on)
    if closes is None or not 0 < index < len(closes):
        msg = f"The demo price history has no price for {symbol} on {on}"
        raise DemoScenarioError(msg)
    days_in_month = calendar.monthrange(on.year, on.month)[1]
    before, after = closes[index - 1], closes[index]
    adjusted = before + (after - before) * Decimal(on.day) / Decimal(days_in_month)
    if symbol == USDCAD:
        return adjusted.quantize(_RATE_PLACES)
    return (adjusted * split_factor(symbol, on)).quantize(_CENT)


@cache
def dividends(symbol: str) -> list[tuple[date, Decimal]]:
    """Every dividend a symbol paid inside the window, per unit held then.

    Args:
        symbol: Folio symbol.

    Returns:
        (ex-date, amount per unit on that date), oldest first.
    """
    events = _parse_events(DIVIDENDS.get(symbol, ""))
    return [(day, amount * split_factor(symbol, day)) for day, amount in events]


def currency_of(symbol: str) -> Currency:
    """Name the currency a symbol trades in."""
    if symbol.endswith(".TO") and symbol not in USD_LINES:
        return Currency.CAD
    return Currency.USD


# -- MARKET CALENDARS ---------------------------------------------------------


@cache
def _sessions(currency: Currency) -> frozenset[date]:
    """Every day the exchange for `currency` trades, across the window."""
    schedule = settlement_calculator.get_calendar_schedule(
        currency,
        pd.Timestamp(START, tz=TORONTO_TZ),
        pd.Timestamp(END, tz=TORONTO_TZ),
    )
    return frozenset(day.date() for day in schedule)


def _open(day: date) -> date:
    """Find the first day on or after `day` that both exchanges trade."""
    while day not in _sessions(Currency.USD) or day not in _sessions(Currency.CAD):
        day += timedelta(days=1)
    return day


def _settles(day: date, currency: Currency) -> date:
    """Find the next session after `day`: when a trade made then settles (T+1)."""
    day += timedelta(days=1)
    while day not in _sessions(currency):
        day += timedelta(days=1)
    return day


def _months(first: date) -> list[date]:
    """List the first of every month from `first` through the end of the window."""
    months: list[date] = []
    while first <= END:
        months.append(first)
        first = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return months


# -- ACCOUNTS -----------------------------------------------------------------


@dataclass(frozen=True)
class _Account:
    """One demo account, and how its broker reports.

    Attributes:
        name: Account name; the folio infers its type from it.
        kind: The account type.
        fee_included: QuestTrade-style: a trade's `Amount` already contains the
            commission. Otherwise the commission is charged on top, IBKR-style.
        broker_settles: The broker reports settlement dates, so the folio keeps
            them instead of calculating its own.
    """

    name: str
    kind: AccountType
    fee_included: bool = False
    broker_settles: bool = False

    @property
    def taxable(self) -> bool:
        """Whether a sale here is a taxable disposition."""
        return self.kind in TAXABLE_ACCOUNT_TYPES

    def fee(self, units: int) -> Decimal:
        """Charge the commission on a trade of `units` units."""
        if self.fee_included:
            return Decimal("4.95")
        return max(Decimal(1), Decimal("0.01") * units).quantize(_CENT)


PERSONAL = _Account("MOCK-PERSONAL", AccountType.NON_REGISTERED)
RRSP = _Account("MOCK-RRSP", AccountType.RRSP)
TFSA = _Account("MOCK-TFSA", AccountType.TFSA)
TFSA2 = _Account(
    "MOCK2-TFSA",
    AccountType.TFSA,
    fee_included=True,
    broker_settles=True,
)
ACCOUNTS: tuple[_Account, ...] = (PERSONAL, RRSP, TFSA, TFSA2)

# Dividends reinvested on the day they are paid.
_DRIP: frozenset[tuple[str, str]] = frozenset({(TFSA.name, "REI.UN.TO")})
_FX_FEE = Decimal(2)
# What a gain or loss must clear, as a share of the cost base, to count as one.
_EXPECTED_MARGIN = Decimal("0.05")


# -- THE SIMULATION -----------------------------------------------------------

_ARRIVAL, _SCRIPTED, _BACKGROUND = 0, 1, 2


@dataclass(order=True, frozen=True)
class _Event:
    """One thing that happens on a day, run in (day, rank, sequence) order."""

    day: date
    rank: int
    sequence: int
    handler: Callable[..., None] = field(compare=False)
    args: tuple[Any, ...] = field(compare=False)
    kwargs: dict[str, Any] = field(compare=False)


def _floor(value: Decimal) -> int:
    """Whole units, rounded down."""
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def _affordable_units(account: _Account, price: Decimal, cash: Decimal) -> int:
    """Count the whole units `cash` pays for, commission included."""
    units = _floor(cash / price)
    while units and price * units + account.fee(units) > cash:
        units -= 1
    return units


class _Simulation:
    """Walk the window a day at a time, writing only rows the folio accepts.

    It tracks what the replay will check, so the folio comes out clean by
    construction: units held (no oversell), the cost base (return of capital
    stays under it), cash (never negative), contribution room (never exceeded)
    and the superficial-loss window around every taxable sale (never entered).

    Cash is booked conservatively: a debit lands on its trade date and a credit
    on its settlement date, so a balance that holds here also holds when the
    replay books both on their settlement dates.
    """

    def __init__(self) -> None:
        """Start with nothing held and nothing scheduled."""
        self.rows: list[dict[str, object]] = []
        self._queue: list[_Event] = []
        self._sequence = count()
        self._units: dict[tuple[str, str], int] = {}
        self._cost: dict[tuple[str, str], Decimal] = {}
        self._cash: dict[tuple[str, Currency], Decimal] = {}
        self._pending: list[tuple[date, str, Currency, Decimal]] = []
        self._room: dict[tuple[str, int], Decimal] = {}
        self._paid: dict[tuple[str, str, int], Decimal] = {}
        self._payouts: list[tuple[date, _Account, str, Decimal]] = []
        self._taxable_sales: list[tuple[str, date]] = []

    # -- scheduling ---------------------------------------------------------

    def at(
        self,
        day: date,
        handler: Callable[..., None],
        *args: object,
        rank: int = _SCRIPTED,
        **kwargs: object,
    ) -> None:
        """Schedule an action for the first trading day on or after `day`.

        Args:
            day: The earliest day it may run.
            handler: A bound method of this simulation, such as `sim.buy`.
            *args: What the action takes after its day.
            rank: Which events run first on the same day.
            **kwargs: What the action takes by keyword.
        """
        when = _open(day)
        if handler == self.sell:
            sale = inspect.signature(handler).bind(when, *args, **kwargs).arguments
            if sale["account"].taxable:
                self._taxable_sales.append((sale["symbol"], when))
        event = _Event(when, rank, next(self._sequence), handler, args, kwargs)
        heapq.heappush(self._queue, event)

    def run(self) -> None:
        """Play every day of the window, then every event scheduled on it."""
        ex_dates: dict[date, list[tuple[str, Decimal]]] = {}
        split_dates: dict[date, list[tuple[str, Decimal]]] = {}
        for symbol in SYMBOLS:
            for day, per_share in dividends(symbol):
                ex_dates.setdefault(day, []).append((symbol, per_share))
            for day, ratio in splits(symbol):
                split_dates.setdefault(day, []).append((symbol, ratio))

        day = START
        while day <= END:
            self._settle(day)
            for symbol, ratio in split_dates.get(day, []):
                self._split(day, symbol, ratio)
            for symbol, per_share in ex_dates.get(day, []):
                self._declare(day, symbol, per_share)
            self._pay(day)
            while self._queue and self._queue[0].day == day:
                event = heapq.heappop(self._queue)
                event.handler(day, *event.args, **event.kwargs)
            day += timedelta(days=1)

    # -- bookkeeping --------------------------------------------------------

    def _emit(  # noqa: PLR0913 - one keyword per transaction column
        self,
        day: date,
        action: Action,
        account: _Account,
        currency: Currency,
        *,
        amount: Decimal | None = None,
        price: Decimal | None = None,
        units: Decimal | int | None = None,
        symbol: str | None = None,
        fee: Decimal | None = None,
    ) -> None:
        """Write one transaction row."""
        settle = None
        if account.broker_settles and action in BUSINESS_DAY_SETTLE_ACTIONS:
            settle = _settles(day, currency).isoformat()
        self.rows.append(
            {
                Column.Txn.TXN_DATE: day.isoformat(),
                Column.Txn.ACTION: action.value,
                Column.Txn.AMOUNT: None if amount is None else float(amount),
                Column.Txn.CURRENCY: currency.value,
                Column.Txn.PRICE: None if price is None else float(price),
                Column.Txn.UNITS: None if units is None else float(units),
                Column.Txn.TICKER: trading_symbol(symbol, day) if symbol else None,
                Column.Txn.ACCOUNT: account.name,
                # Both brokers write a charged commission as a negative number.
                Column.Txn.FEE: float(-fee) if fee else None,
                Column.Txn.SETTLE_DATE: settle,
            },
        )

    def _balance(self, account: _Account, currency: Currency) -> Decimal:
        """Settled cash in one account and currency."""
        return self._cash.get((account.name, currency), Decimal(0))

    def _add(self, account: _Account, currency: Currency, amount: Decimal) -> None:
        """Move settled cash, refusing to overdraw."""
        balance = self._balance(account, currency) + amount
        if balance < 0:
            msg = f"{account.name} {currency} cash would fall to {balance}"
            raise DemoScenarioError(msg)
        self._cash[(account.name, currency)] = balance

    def _add_later(
        self,
        settles: date,
        account: _Account,
        currency: Currency,
        amount: Decimal,
    ) -> None:
        """Credit cash once the trade that raised it settles."""
        self._pending.append((settles, account.name, currency, amount))

    def _settle(self, day: date) -> None:
        """Release every credit settling today."""
        due = [entry for entry in self._pending if entry[0] <= day]
        self._pending = [entry for entry in self._pending if entry[0] > day]
        for _, name, currency, amount in due:
            key = (name, currency)
            self._cash[key] = self._cash.get(key, Decimal(0)) + amount

    def _held(self, account: _Account, symbol: str) -> int:
        """Units of `symbol` held in `account`."""
        return self._units.get((account.name, symbol), 0)

    def _blacked_out(self, symbol: str, day: date) -> bool:
        """Whether a purchase today would make a taxable sale a superficial loss."""
        window = timedelta(days=SUPERFICIAL_LOSS_DAYS)
        return any(
            sold == symbol and abs(day - on) <= window
            for sold, on in self._taxable_sales
        )

    # -- what the market does -------------------------------------------------

    def _split(self, day: date, symbol: str, ratio: Decimal) -> None:
        """Record a split in every account holding the symbol that day."""
        for account in ACCOUNTS:
            held = self._held(account, symbol)
            if held:
                self._units[(account.name, symbol)] = _floor(held * ratio)
                self._emit(
                    day,
                    Action.SPLIT,
                    account,
                    currency_of(symbol),
                    price=Decimal(1),
                    units=ratio,
                    symbol=symbol,
                )

    def _declare(self, day: date, symbol: str, per_share: Decimal) -> None:
        """Owe a dividend to every account holding the symbol going into its ex-date.

        It is paid two weeks later, unless that falls after the window.
        """
        pays = _open(day + timedelta(days=14))
        if pays > END:
            return
        for account in ACCOUNTS:
            amount = (self._held(account, symbol) * per_share).quantize(_CENT)
            if amount > 0:
                self._payouts.append((pays, account, symbol, amount))

    def _pay(self, day: date) -> None:
        """Pay every dividend due today, reinvesting it where the account does."""
        due = [payout for payout in self._payouts if payout[0] == day]
        self._payouts = [payout for payout in self._payouts if payout[0] != day]
        for _, account, symbol, amount in due:
            currency = currency_of(symbol)
            self._emit(
                day,
                Action.DIVIDEND,
                account,
                currency,
                amount=amount,
                symbol=symbol,
            )
            self._add(account, currency, amount)
            key = (account.name, symbol, day.year)
            self._paid[key] = self._paid.get(key, Decimal(0)) + amount
            if (account.name, symbol) in _DRIP:
                units = _floor(amount / price_on(symbol, day))
                if units:
                    self.buy(day, account, symbol, units=units, commission=False)

    # -- what the investor does -----------------------------------------------

    def contribute(self, day: date, account: _Account, amount: int) -> None:
        """Contribute new money, within the account type's contribution room."""
        cash = Decimal(amount)
        limits = CRA_ROOM.get(account.kind.value)
        if limits is not None:
            year = room_year(account.kind, day)
            key = (account.kind.value, year)
            self._room[key] = self._room.get(key, Decimal(0)) + cash
            if self._room[key] > limits[year]:
                msg = f"{account.kind} contributions for {year} pass the CRA limit"
                raise DemoScenarioError(msg)
        self._emit(day, Action.CONTRIBUTION, account, Currency.CAD, amount=cash)
        self._add(account, Currency.CAD, cash)

    def withdraw(self, day: date, account: _Account, amount: int) -> None:
        """Take money out of the folio."""
        cash = Decimal(amount)
        self._add(account, Currency.CAD, -cash)
        self._emit(day, Action.WITHDRAWAL, account, Currency.CAD, amount=-cash)

    def move(self, day: date, source: _Account, target: _Account, amount: int) -> None:
        """Move cash from a taxable account into a registered one.

        Across account types the CRA sees a withdrawal and a fresh contribution,
        which uses room, never a transfer.
        """
        self.withdraw(day, source, amount)
        self.contribute(day, target, amount)

    def charge(
        self,
        day: date,
        account: _Account,
        amount: Decimal,
        currency: Currency = Currency.CAD,
    ) -> None:
        """Book a fee (negative) or interest (positive) as a financial charge."""
        self._add(account, currency, amount)
        self._emit(day, Action.FCH, account, currency, amount=amount)

    def interest(self, day: date, account: _Account) -> None:
        """Pay a quarter's interest on idle Canadian cash, at 2.5% a year."""
        earned = (self._balance(account, Currency.CAD) * Decimal("0.00625")).quantize(
            _CENT,
        )
        if earned > 0:
            self.charge(day, account, earned)

    def convert(self, day: date, account: _Account, keep: int) -> None:
        """Buy US dollars with the Canadian cash above `keep`, IBKR-style.

        One row: `Amount` is the Canadian side, `Units` the US dollars bought,
        `Price` the rate, and the commission is charged in US dollars.
        """
        rate = price_on(USDCAD, day)
        spare = self._balance(account, Currency.CAD) - keep
        usd = _floor(spare / rate / 100) * 100
        if usd <= 0:
            msg = f"{account.name} has no Canadian cash to convert on {day}"
            raise DemoScenarioError(msg)
        cad = (usd * rate).quantize(_CENT)
        self._add(account, Currency.CAD, -cad)
        self._add_later(
            _settles(day, Currency.CAD),
            account,
            Currency.USD,
            usd - _FX_FEE,
        )
        self._emit(
            day,
            Action.FXT,
            account,
            Currency.CAD,
            amount=-cad,
            price=rate,
            units=usd,
            fee=_FX_FEE,
        )

    def buy(  # noqa: PLR0913 - units or a budget, and who is buying
        self,
        day: date,
        account: _Account,
        symbol: str,
        units: int | None = None,
        budget: int | None = None,
        *,
        commission: bool = True,
    ) -> None:
        """Buy a number of units, or as many as a budget and the cash allow.

        A budgeted purchase is background activity: it quietly skips a day that
        would make a taxable sale a superficial loss. A scripted purchase on
        such a day is a mistake in the script.
        """
        currency = currency_of(symbol)
        price = price_on(symbol, day)
        if self._blacked_out(symbol, day):
            if budget is not None:
                return
            msg = f"Buying {symbol} on {day} makes a taxable sale a superficial loss"
            raise DemoScenarioError(msg)
        if units is None:
            spend = min(Decimal(budget or 0), self._balance(account, currency))
            units = _affordable_units(account, price, spend)
            if not units:
                return
        gross = (price * units).quantize(_CENT)
        fee = account.fee(units) if commission else Decimal(0)
        self._add(account, currency, -(gross + fee))
        key = (account.name, symbol)
        self._units[key] = self._held(account, symbol) + units
        self._cost[key] = self._cost.get(key, Decimal(0)) + gross + fee
        amount = gross + fee if account.fee_included else gross
        self._emit(
            day,
            Action.BUY,
            account,
            currency,
            amount=-amount,
            price=price,
            units=units,
            symbol=symbol,
            fee=fee,
        )

    def sell(
        self,
        day: date,
        account: _Account,
        symbol: str,
        units: int | None = None,
        expect: str | None = None,
    ) -> None:
        """Sell some units, or the whole holding, checking the outcome is as meant.

        Args:
            day: Trade date.
            account: Who sells.
            symbol: What is sold.
            units: How many; the whole holding when None.
            expect: "gain" or "loss" when the script means to show one.

        Raises:
            DemoScenarioError: When the holding is short of `units`, or the
                sale does not come out as `expect` says.
        """
        key = (account.name, symbol)
        held = self._held(account, symbol)
        units = held if units is None else units
        if not 0 < units <= held:
            msg = f"{account.name} cannot sell {units} {symbol}, holding {held}"
            raise DemoScenarioError(msg)
        currency = currency_of(symbol)
        price = price_on(symbol, day)
        gross = (price * units).quantize(_CENT)
        fee = account.fee(units)
        removed = self._cost[key] * units / held
        gain = gross - fee - removed
        if expect and (gain * (1 if expect == "gain" else -1)) < (
            removed * _EXPECTED_MARGIN
        ):
            msg = f"Selling {symbol} on {day} is meant to show a {expect}: {gain}"
            raise DemoScenarioError(msg)
        self._units[key] = held - units
        self._cost[key] -= removed
        self._add_later(_settles(day, currency), account, currency, gross - fee)
        amount = gross - fee if account.fee_included else gross
        self._emit(
            day,
            Action.SELL,
            account,
            currency,
            amount=amount,
            price=price,
            units=-units,
            symbol=symbol,
            fee=fee,
        )

    def roc(self, day: date, account: _Account, symbol: str, share: str) -> None:
        """Reclassify part of the year's distributions as return of capital.

        No cash moves: it was paid as dividends already. Only the cost base
        drops, and never below zero.
        """
        paid = self._paid.get((account.name, symbol, day.year), Decimal(0))
        amount = (paid * Decimal(share)).quantize(_CENT)
        key = (account.name, symbol)
        if not 0 < amount < self._cost.get(key, Decimal(0)):
            msg = f"Return of capital on {symbol} would not fit its cost base"
            raise DemoScenarioError(msg)
        self._cost[key] -= amount
        self._emit(
            day,
            Action.ROC,
            account,
            currency_of(symbol),
            amount=amount,
            symbol=symbol,
        )

    def gambit(self, day: date, account: _Account, keep: int) -> None:
        """Norbert's gambit, step one: buy DLR with the Canadian cash above `keep`.

        The units are journalled to DLR.U the next day and sold for US dollars
        the day after, a far cheaper conversion than a broker's FX desk.
        """
        price = price_on("DLR.TO", day)
        spare = self._balance(account, Currency.CAD) - keep
        units = _affordable_units(account, price, spare)
        if units <= 0:
            msg = f"{account.name} has no Canadian cash for a gambit on {day}"
            raise DemoScenarioError(msg)
        self.buy(day, account, "DLR.TO", units=units)
        self.at(day + timedelta(days=1), self.journal, account, units, rank=_ARRIVAL)

    def journal(self, day: date, account: _Account, units: int) -> None:
        """Norbert's gambit, step two: journal DLR across to its US dollar line."""
        source, target = (account.name, "DLR.TO"), (account.name, "DLR.U.TO")
        moved = self._cost[source] * units / self._held(account, "DLR.TO")
        self._units[source] -= units
        self._units[target] = self._held(account, "DLR.U.TO") + units
        self._cost[source] -= moved
        self._cost[target] = self._cost.get(target, Decimal(0)) + moved / price_on(
            USDCAD,
            day,
        )
        self._emit(
            day,
            Action.TFR_OUT,
            account,
            Currency.CAD,
            units=-units,
            symbol="DLR.TO",
        )
        self._emit(
            day,
            Action.TFR_IN,
            account,
            Currency.USD,
            units=units,
            symbol="DLR.U.TO",
        )
        self.at(day + timedelta(days=1), self.sell, account, "DLR.U.TO", rank=_ARRIVAL)

    def transfer_all(self, day: date, source: _Account, target: _Account) -> None:
        """Move an account's every holding and its cash to another, in kind.

        The out legs leave today; the in legs arrive three days later, the way
        an institutional transfer between brokers lands.
        """
        arrives = day + timedelta(days=3)
        for (name, symbol), held in sorted(self._units.items()):
            if name != source.name or not held:
                continue
            cost = self._cost.pop((name, symbol))
            self._units[(name, symbol)] = 0
            currency = currency_of(symbol)
            self._emit(
                day,
                Action.TFR_OUT,
                source,
                currency,
                units=-held,
                symbol=symbol,
            )
            self.at(arrives, self.arrive, target, symbol, held, cost, rank=_ARRIVAL)
        cash = self._balance(source, Currency.CAD)
        if cash > 0:
            self._add(source, Currency.CAD, -cash)
            self._emit(day, Action.TFR_OUT, source, Currency.CAD, amount=-cash)
            self.at(arrives, self.arrive, target, None, 0, cash, rank=_ARRIVAL)

    def arrive(
        self,
        day: date,
        target: _Account,
        symbol: str | None,
        units: int,
        value: Decimal,
    ) -> None:
        """Land one leg of a transfer: a holding and its cost base, or cash."""
        if symbol is None:
            self._add(target, Currency.CAD, value)
            self._emit(day, Action.TFR_IN, target, Currency.CAD, amount=value)
            return
        key = (target.name, symbol)
        self._units[key] = self._held(target, symbol) + units
        self._cost[key] = self._cost.get(key, Decimal(0)) + value
        self._emit(
            day,
            Action.TFR_IN,
            target,
            currency_of(symbol),
            units=units,
            symbol=symbol,
        )


# -- THE SCRIPT ---------------------------------------------------------------


def _script(sim: _Simulation) -> None:
    """Every hand-placed event, each one there to show something."""
    at = sim.at

    # Day one: every account opens with a contribution. The TFSA at the second broker
    # takes 2024's whole TFSA room.
    at(START, sim.contribute, PERSONAL, 60_000)
    at(START, sim.contribute, RRSP, 15_000)
    at(START, sim.contribute, TFSA2, 7_000)

    # US dollars two ways: Norbert's gambit in the taxable account, the
    # broker's own conversion (with its fee) in the RRSP.
    at(START, sim.gambit, PERSONAL, keep=12_000)
    at(START, sim.convert, RRSP, keep=1_500)

    # SCHD bought days before its 3:1 split; SPY and QQQ alongside it.
    at(date(2024, 10, 3), sim.buy, RRSP, "SCHD", units=30)
    at(date(2024, 10, 3), sim.buy, RRSP, "SPY", units=5)
    at(date(2024, 10, 3), sim.buy, RRSP, "QQQ", units=5)

    # The second broker prices its commission into each trade's amount and
    # reports its own settlement dates.
    for symbol, units in (("RY.TO", 10), ("TD.TO", 20), ("ENB.TO", 30), ("CNR.TO", 10)):
        at(date(2024, 10, 2), sim.buy, TFSA2, symbol, units=units)

    # The taxable account's core holdings, once the gambit's US dollars land.
    for symbol, units in (
        ("META", 10),
        ("NVDA", 60),
        ("AAPL", 20),
        ("MSFT", 10),
        ("KO", 30),
        ("JNJ", 15),
        ("O", 50),
    ):
        at(date(2024, 10, 7), sim.buy, PERSONAL, symbol, units=units)

    # Moving money into a TFSA from a taxable account: a withdrawal and a contribution,
    # which uses the year's room, never a transfer.
    at(date(2025, 1, 6), sim.move, PERSONAL, TFSA, 7_000)
    at(date(2026, 1, 6), sim.move, PERSONAL, TFSA, 7_000)

    # A REIT bought for income: its distributions are reinvested (DRIP), and
    # part of each year's turns out to be return of capital.
    at(date(2025, 1, 8), sim.buy, TFSA, "REI.UN.TO", units=300)
    at(date(2025, 12, 31), sim.roc, TFSA, "REI.UN.TO", share="0.30")
    at(date(2024, 12, 31), sim.roc, PERSONAL, "O", share="0.08")
    at(date(2025, 12, 31), sim.roc, PERSONAL, "O", share="0.08")

    # An RRSP contribution in January or February counts toward last year.
    at(date(2025, 2, 10), sim.contribute, RRSP, 5_000)
    at(date(2025, 3, 3), sim.convert, RRSP, keep=300)

    # NFLX held in two accounts through its 10:1 split, so the split is
    # recorded once per account but applied once at the type and folio grains.
    at(date(2025, 3, 5), sim.buy, RRSP, "NFLX", units=1)
    at(date(2025, 4, 11), sim.buy, PERSONAL, "NFLX", units=1)

    # Taxable sales: a holding closed at a loss, a partial sale and a close-out
    # at a gain. US dollar sales carry the currency's own gain or loss too.
    at(date(2025, 2, 25), sim.buy, PERSONAL, "INTC", units=50)
    at(date(2025, 4, 29), sim.sell, PERSONAL, "INTC", expect="loss")
    at(date(2025, 10, 14), sim.sell, PERSONAL, "NVDA", units=30, expect="gain")
    at(date(2026, 2, 17), sim.sell, PERSONAL, "META", expect="gain")

    # Consolidating brokers: the second TFSA moves everything to the first, in
    # kind, and is left empty.
    at(date(2025, 9, 15), sim.transfer_all, TFSA2, TFSA)

    # A withdrawal from a TFSA: room comes back the next year.
    at(date(2026, 6, 1), sim.sell, TFSA, "VFV.TO", units=10)
    at(date(2026, 6, 15), sim.withdraw, TFSA, 1_500)

    # Account charges: an annual fee, and interest on idle cash.
    for year in (2025, 2026):
        at(date(year, 1, 15), sim.charge, RRSP, Decimal(-50))
    for month in _months(date(2024, 12, 1))[::3]:
        at(month - timedelta(days=1), sim.interest, PERSONAL)


_PERSONAL_ROTATION = ("AAPL", "MSFT", "NVDA", "COST", "KO", "JNJ", "XYZ", "O", "NFLX")
_RRSP_ROTATION = ("SPY", "QQQ", "SCHD")
_TFSA_ROTATION = ("XEQT.TO", "VFV.TO")
_NFLX_FROM = date(2025, 4, 1)


@dataclass(frozen=True)
class _Routine:
    """What one account does every month.

    Attributes:
        account: Whose routine it is.
        deposit: Contributed on the first trading day of each month.
        buy_days: Days of the month a purchase is made.
        rotation: Symbols bought in turn, one per purchase.
        budget: The most one purchase spends; less when cash is short.
    """

    account: _Account
    deposit: int
    buy_days: tuple[int, ...]
    rotation: tuple[str, ...]
    budget: int


_ROUTINES = (
    _Routine(PERSONAL, 4_000, (5, 12, 19, 26), _PERSONAL_ROTATION, 1_200),
    _Routine(RRSP, 2_000, (6, 20), _RRSP_ROTATION, 1_600),
    _Routine(TFSA, 0, (12, 26), _TFSA_ROTATION, 300),
)
# Canadian cash the taxable account keeps back from each gambit. December's
# covers January's TFSA contribution.
_GAMBIT_KEEP = {2: 2_000, 4: 2_000, 6: 2_000, 8: 2_000, 10: 2_000, 12: 5_000}


def _background(sim: _Simulation) -> None:
    """Fill every month with the steady activity a real folio has."""
    turns = dict.fromkeys(_ROUTINES, 0)
    for month in _months(date(2024, 11, 1)):
        for routine in _ROUTINES:
            if routine.deposit:
                sim.at(
                    month,
                    sim.contribute,
                    routine.account,
                    routine.deposit,
                    rank=_BACKGROUND,
                )
            for day in routine.buy_days:
                symbol = routine.rotation[turns[routine] % len(routine.rotation)]
                turns[routine] += 1
                if symbol == "NFLX" and month < _NFLX_FROM:
                    continue
                sim.at(
                    month.replace(day=day),
                    sim.buy,
                    routine.account,
                    symbol,
                    budget=routine.budget,
                    rank=_BACKGROUND,
                )
        if month.month % 2:
            sim.at(month.replace(day=3), sim.convert, RRSP, keep=300, rank=_BACKGROUND)
        else:
            keep = _GAMBIT_KEEP[month.month]
            sim.at(
                month.replace(day=8),
                sim.gambit,
                PERSONAL,
                keep=keep,
                rank=_BACKGROUND,
            )


# -- THE DEMO FOLIO -----------------------------------------------------------


class DemoFolio:
    """Generate the demo folio and write it into the folio's database."""

    @staticmethod
    def rows() -> pd.DataFrame:
        """Generate every demo transaction, without touching the database.

        Returns:
            The rows, keyed by the folio's own column names.

        Raises:
            DemoScenarioError: If two rows would be the same transaction.
        """
        sim = _Simulation()
        _script(sim)
        _background(sim)
        sim.run()
        columns = [*TXN_ESSENTIALS, Column.Txn.FEE, Column.Txn.SETTLE_DATE]
        frame = pd.DataFrame(sim.rows, columns=columns)
        if not generate_keys(frame).is_unique:
            msg = "The demo generated two identical transactions"
            raise DemoScenarioError(msg)
        return frame

    def ensure(self) -> bool:
        """Build the demo folio, unless the folio already holds anything.

        Returns:
            True when the demo was built, False when data already existed.

        Raises:
            FileNotFoundError: If the folio's folder is outside the default data
                folder and does not exist.
        """
        config = get_config()
        if config.txn_parquet.exists():
            console_warning(f'Transaction data already exists: "{config.txn_parquet}"')
            return False

        if config.db_path.exists():
            create_txns_table()
            with get_connection() as conn:
                if get_row_count(conn, Table.TXNS) > 0:
                    console_warning(
                        "Transaction data already exists in the database: "
                        f'"{config.db_path}". Back up or delete this file first '
                        "if you want to generate a fresh demo folio.",
                    )
                    return False

        folder = config.folio_path.parent
        if folder.is_relative_to(config.project_root / "data"):
            folder.mkdir(parents=True, exist_ok=True)
        elif not folder.exists():
            msg = f'MISSING folder: "{folder}"'
            raise FileNotFoundError(msg)

        self.build()
        return True

    def build(self) -> None:
        """Write the demo into the folio: transactions, renames, room and rates.

        A build that fails part way removes what it wrote, so the next attempt
        starts from an empty folio rather than refusing to run.

        Raises:
            DemoScenarioError: If the import pipeline turns away any demo row.
        """
        rows = self.rows()
        ForexService.ensure_coverage(start=START.isoformat())
        try:
            self._write(rows)
        except Exception:
            self._remove()
            raise
        logger.info("CREATED demo folio: %d transactions", len(rows))

    @staticmethod
    def _write(rows: pd.DataFrame) -> None:
        """Store the demo's transactions and renames, then export the folio."""
        results = insert_transactions(rows, map_headers=False)
        if len(results.final_df) != len(rows):
            msg = f"The import kept {len(results.final_df)} of {len(rows)} demo rows"
            raise DemoScenarioError(msg)

        with get_connection() as conn:
            for old, new, effective in RENAMES:
                insert_or_replace(
                    conn,
                    Table.TICKER_ALIASES,
                    {
                        Column.Aliases.OLD_TICKER: old,
                        Column.Aliases.NEW_TICKER: new,
                        Column.Aliases.EFFECTIVE_DATE: effective.isoformat(),
                    },
                )

        config = get_config()
        if write_missing_setting(config.config_path, "contribution_room", CRA_ROOM):
            initialize_app(config.project_root)

        ParquetExporter().export_all()

    @staticmethod
    def clear() -> None:
        """Remove the demo, so real transactions start an empty folio from TxnId 1.

        The folio is backed up first. Exchange rates and cached quotes stay, being
        real data, and so does any contribution room the demo wrote to config.
        """
        backup_folio()
        DemoFolio._remove()
        logger.info("REMOVED demo folio")

    @staticmethod
    def _remove() -> None:
        """Drop the folio's transactions and ticker aliases, and their exports."""
        with get_connection() as conn:
            drop_table(conn, Table.TXNS)
            drop_table(conn, Table.TICKER_ALIASES)
        config = get_config()
        config.txn_parquet.unlink(missing_ok=True)
        config.tkr_parquet.unlink(missing_ok=True)


def _is_demo_account(name: str) -> bool:
    """Whether an account name is one the demo uses."""
    return bool(_DEMO_ACCOUNT.match(name))


def is_demo_folio() -> bool:
    """Whether the folio holds the demo and nothing else.

    Returns:
        True when it has transactions and every one is in a demo account. A
        folio the user has added any account of their own to is theirs.
    """
    with get_connection() as conn:
        accounts = get_distinct_set(conn, Table.TXNS, Column.Txn.ACCOUNT)
    return bool(accounts) and all(_is_demo_account(str(name)) for name in accounts)


def ensure_data_exists() -> bool:
    """Build the demo folio unless the folio already holds data.

    Returns:
        True when the demo was built.
    """
    return DemoFolio().ensure()
