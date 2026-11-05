"""Settlement date calculation logic."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, cast

import numpy as np
import pandas as pd

from domain import TORONTO_TZ, WEEKDAYS, Action, Column, Currency

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

DATE_FORMAT: str = "%Y-%m-%d"
DATE_PATTERN: str = r"^\d{4}-\d{2}-\d{2}$"
_EMPTY_SCHEDULE: pd.DatetimeIndex = pd.DatetimeIndex([])
_EXCHANGES: dict[Currency, str] = {Currency.USD: "NYSE", Currency.CAD: "TSX"}
T_PLUS_1_EFFECTIVE_DATES: dict[Currency, date] = {
    Currency.USD: datetime(2024, 5, 28, tzinfo=TORONTO_TZ).date(),  # US markets
    Currency.CAD: datetime(2024, 5, 27, tzinfo=TORONTO_TZ).date(),  # Canadian markets
}

SAME_DAY_SETTLE_ACTIONS: set[Action] = {
    Action.DIVIDEND,
    Action.CONTRIBUTION,
    Action.FCH,
    Action.ROC,
    Action.TFR_IN,
    Action.TFR_OUT,
    Action.WITHDRAWAL,
}

BUSINESS_DAY_SETTLE_ACTIONS: set[Action] = {
    Action.BUY,
    Action.SELL,
    Action.FXT,
    Action.SPLIT,
}


class TradingCalendar:
    """The days each currency's exchange trades.

    Fixed sessions, handed in, are all it ever knows. Otherwise it asks the
    market calendar library, loaded on first use and cached by date range.
    """

    def __init__(
        self,
        sessions: Mapping[Currency, list[str]] | None = None,
    ) -> None:
        """Create a calendar.

        Args:
            sessions: Each currency's trading days as YYYY-MM-DD, or None for
                the real exchanges.
        """
        self._is_fixed = sessions is not None
        self._schedules: dict[Currency, pd.DatetimeIndex] = {
            currency: pd.DatetimeIndex(days)
            for currency, days in (sessions or {}).items()
        }

    def trading_days(
        self,
        currency: Currency,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
    ) -> pd.DatetimeIndex:
        """Get the trading days covering a date range, loading them if needed.

        A cached schedule that does not cover the range is replaced by one
        spanning both, so widening never drops days already loaded.

        Args:
            currency: Currency whose exchange applies.
            start_date: Start of the range.
            end_date: End of the range.

        Returns:
            Every trading day known for the currency, which may reach beyond
            the range. Empty for a currency with no exchange.
        """
        existing = self._schedules.get(currency, _EMPTY_SCHEDULE)
        exchange = _EXCHANGES.get(currency)
        if self._is_fixed or exchange is None:
            return existing

        if len(existing) > 0:
            cached_start = existing[0].tz_localize(TORONTO_TZ)
            cached_end = existing[-1].tz_localize(TORONTO_TZ)
            if cached_start <= start_date and cached_end >= end_date:
                return existing
            start_date = min(start_date, cached_start)
            end_date = max(end_date, cached_end)

        import pandas_market_calendars as mcal

        schedule: pd.DataFrame = mcal.get_calendar(exchange).schedule(
            start_date=start_date - pd.DateOffset(days=10),
            end_date=end_date + pd.DateOffset(days=30),
        )
        days = pd.DatetimeIndex(schedule.index)
        self._schedules[currency] = days
        return days

    def nth_trading_day_after(
        self,
        day: pd.Timestamp,
        count: int,
        currency: Currency,
    ) -> str:
        """Return the Nth trading day strictly after a date.

        A currency with no exchange, or a count running past the last known
        trading day, counts plain weekdays instead.

        Args:
            day: The date to count from.
            count: How many trading days to count.
            currency: Currency whose exchange applies.

        Returns:
            The date in YYYY-MM-DD format.
        """
        schedule = self._schedules.get(currency, _EMPTY_SCHEDULE)
        if len(schedule) == 0 or not schedule[0] <= day <= schedule[-1]:
            when = day.tz_localize(TORONTO_TZ)
            schedule = self.trading_days(currency, when, when)
        if len(schedule) == 0:
            return _nth_weekday_after(day, count)

        position = int(schedule.searchsorted(day, side="right"))
        target = position + count - 1
        if target >= len(schedule):  # pragma: no cover
            return _nth_weekday_after(day, count)
        return schedule[target].strftime(DATE_FORMAT)


def _nth_weekday_after(day: pd.Timestamp, count: int) -> str:
    """Return the Nth Monday-to-Friday strictly after a date, as YYYY-MM-DD."""
    current = day
    counted = 0
    while counted < count:
        current += pd.DateOffset(days=1)
        if current.weekday() < WEEKDAYS:
            counted += 1
    return current.strftime(DATE_FORMAT)


class SettlementCalculator:
    """Calculates settlement dates for transactions based on business rules."""

    def __init__(self, calendar: TradingCalendar | None = None) -> None:
        """Initialize the settlement calculator.

        Args:
            calendar: The trading days to settle against, or None for the real
                exchanges.
        """
        self.calendar = calendar or TradingCalendar()

    def add_settlement_dates_to_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add and calculate settlement dates in the DataFrame.

        This method processes the DataFrame and adds/updates settlement date
        and settlement calculated columns based on the market calendars.

        Args:
            df: DataFrame with transaction data

        Returns:
            DataFrame with settlement date columns added/updated
        """
        if df.empty:
            return df

        df_copy = df.copy()
        if Column.Txn.SETTLE_DATE not in df_copy.columns:
            df_copy[Column.Txn.SETTLE_DATE] = pd.NA
        if Column.Txn.SETTLE_CALCULATED not in df_copy.columns:
            df_copy[Column.Txn.SETTLE_CALCULATED] = 0

        needs_calculation = df_copy[Column.Txn.SETTLE_DATE].isna() | ~df_copy[
            Column.Txn.SETTLE_DATE
        ].astype(str).str.match(DATE_PATTERN)
        has_valid_txn_date = ~df_copy[Column.Txn.TXN_DATE].isna()
        needs_calculation = needs_calculation & has_valid_txn_date

        if not needs_calculation.any():
            return df_copy

        # Pre-load schedules for the date range if not already cached
        min_date = df_copy[Column.Txn.TXN_DATE].min()
        max_date = df_copy[Column.Txn.TXN_DATE].max()
        if not pd.isna(min_date) and not pd.isna(max_date):
            start_ts = pd.Timestamp(min_date).tz_localize(TORONTO_TZ)
            end_ts = pd.Timestamp(max_date).tz_localize(TORONTO_TZ)
            self._ensure_schedules_loaded(start_ts, end_ts)

        self._write_settlement_columns(df_copy, needs_calculation)
        return df_copy

    def _write_settlement_columns(
        self,
        df: pd.DataFrame,
        needs_calculation: pd.Series,
    ) -> None:
        """Settle every flagged row, then write both columns in one assignment.

        Each column is read out to an array once and written back once.

        Args:
            df: Frame to update in place. Already carries both settlement
                columns.
            needs_calculation: Rows whose settlement date has to be derived.
        """
        txn_dates = df[Column.Txn.TXN_DATE].to_numpy()
        actions: list[str] = df[Column.Txn.ACTION].tolist()
        currencies: list[str] = df[Column.Txn.CURRENCY].tolist()
        settle_dates = df[Column.Txn.SETTLE_DATE].astype(object).to_numpy(copy=True)
        calculated = df[Column.Txn.SETTLE_CALCULATED].to_numpy(copy=True)

        rows: list[int] = np.flatnonzero(needs_calculation.to_numpy()).tolist()
        # One parse for the whole batch: pandas' date parser has a high fixed
        # cost but a very low per-value one.
        timestamps = pd.to_datetime(txn_dates[rows], format=DATE_FORMAT)

        for offset, row in enumerate(rows):
            timestamp = timestamps[offset]
            # Currency is a StrEnum, so the plain string held in the frame
            # hashes and compares equal to the enum keying the schedules.
            currency = cast("Currency", currencies[row])
            settlement_days, is_calculated = self._get_settlement_days(
                Action(actions[row]),
                currency,
                timestamp.date(),
            )
            if settlement_days == 0:
                settle_dates[row] = txn_dates[row]
            else:
                settle_dates[row] = self.calendar.nth_trading_day_after(
                    timestamp,
                    settlement_days,
                    currency,
                )
            calculated[row] = int(is_calculated)

        df[Column.Txn.SETTLE_DATE] = settle_dates
        df[Column.Txn.SETTLE_CALCULATED] = calculated

    def _ensure_schedules_loaded(
        self,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
    ) -> None:
        """Ensure calendar schedules are loaded for the given date range.

        A schedule already cached is widened rather than trusted: a batch
        dated before it would otherwise settle against the wrong days.

        Args:
            start_date: Start date (timezone-aware)
            end_date: End date (timezone-aware)
        """
        for currency in _EXCHANGES:
            self.calendar.trading_days(currency, start_date, end_date)

    def _get_settlement_days(
        self,
        action: Action,
        currency: Currency,
        txn_date: date,
    ) -> tuple[int, bool]:
        """Get number of settlement days for a transaction.

        Args:
            action: Transaction action
            currency: Transaction currency
            txn_date: Trade date

        Returns:
            Tuple of (settlement days, whether the date is calculated)
        """
        if action in SAME_DAY_SETTLE_ACTIONS:
            return 0, False

        if action in BUSINESS_DAY_SETTLE_ACTIONS:
            # Check if T+1 is effective for this currency and date
            effective_date = T_PLUS_1_EFFECTIVE_DATES.get(currency)
            if effective_date and txn_date >= effective_date:
                return 1, True  # T+1 settlement
            return 2, True  # T+2 settlement

        return 0, False  # pragma: no cover


# Global instance for use throughout the application
settlement_calculator = SettlementCalculator()
