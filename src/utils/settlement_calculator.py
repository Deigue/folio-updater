"""Settlement date calculation utilities."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, cast

import numpy as np
import pandas as pd

from utils.constants import TORONTO_TZ, Action, Column, Currency
from utils.logging_setup import get_import_logger

if TYPE_CHECKING:
    from datetime import date

    import pandas_market_calendars as mcal

import_logger = get_import_logger()

WEEKDAYS_IN_WEEK: int = 5
DATE_FORMAT: str = "%Y-%m-%d"
DATE_PATTERN: str = r"^\d{4}-\d{2}-\d{2}$"
_EMPTY_SCHEDULE: pd.DatetimeIndex = pd.DatetimeIndex([])
T_PLUS_1_EFFECTIVE_DATES: dict[Currency, date] = {
    Currency.USD: datetime(2024, 5, 28, tzinfo=TORONTO_TZ).date(),  # US markets
    Currency.CAD: datetime(2024, 5, 27, tzinfo=TORONTO_TZ).date(),  # Canadian markets
}

SAME_DAY_SETTLE_ACTIONS: set[Action] = {
    Action.DIVIDEND,
    Action.BRW,
    Action.CONTRIBUTION,
    Action.FCH,
    Action.ROC,
    Action.WITHDRAWAL,
}

BUSINESS_DAY_SETTLE_ACTIONS: set[Action] = {
    Action.BUY,
    Action.SELL,
    Action.FXT,
    Action.SPLIT,
}


class SettlementCalculator:
    """Calculates settlement dates for transactions based on business rules."""

    def __init__(self) -> None:
        """Initialize the settlement calculator with market calendars."""
        import pandas_market_calendars as mcal

        self._calendars: dict[Currency, mcal.MarketCalendar] = {}
        self.calendar_schedules: dict[Currency, pd.DatetimeIndex] = {}
        # Initialize market calendars
        self._calendars[Currency.USD] = mcal.get_calendar("NYSE")
        self._calendars[Currency.CAD] = mcal.get_calendar("TSX")

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
                settle_dates[row] = self._nth_trading_day_after(
                    timestamp,
                    settlement_days,
                    currency,
                )
            calculated[row] = is_calculated

        df[Column.Txn.SETTLE_DATE] = settle_dates
        df[Column.Txn.SETTLE_CALCULATED] = calculated

    def _nth_trading_day_after(
        self,
        timestamp: pd.Timestamp,
        settlement_days: int,
        currency: Currency,
    ) -> str:
        """Return the Nth trading day strictly after a transaction date.

        Args:
            timestamp: Transaction date.
            settlement_days: Trading days until settlement.
            currency: Currency whose market calendar applies.

        Returns:
            Settlement date in YYYY-MM-DD format.
        """
        schedule = self.calendar_schedules.get(currency, _EMPTY_SCHEDULE)
        if len(schedule) == 0:  # pragma: no cover
            return self.calculate_simple_business_days(
                timestamp.date(),
                settlement_days,
            )

        position = int(schedule.searchsorted(timestamp, side="right"))
        target = position + settlement_days - 1
        if target >= len(schedule):  # pragma: no cover
            # Calendar ran out before settlement; fall back to plain weekdays.
            return self.calculate_simple_business_days(
                timestamp.date(),
                settlement_days,
            )
        return schedule[target].strftime(DATE_FORMAT)

    def _ensure_schedules_loaded(
        self,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
    ) -> None:
        """Ensure calendar schedules are loaded for the given date range.

        Args:
            start_date: Start date (timezone-aware)
            end_date: End date (timezone-aware)
        """
        # Check if we need to load/expand schedules
        for currency in [Currency.USD, Currency.CAD]:
            if (
                currency not in self.calendar_schedules
                or len(self.calendar_schedules[currency]) == 0
            ):
                # Load schedule with buffer
                buffer_start = start_date - pd.Timedelta(days=10)
                buffer_end = end_date + pd.Timedelta(days=30)
                self.calendar_schedules[currency] = self.get_calendar_schedule(
                    currency,
                    buffer_start,
                    buffer_end,
                )

    def calculate_simple_business_days(
        self,
        txn_date: date,
        settlement_days: int,
    ) -> str:
        """Calculate settlement date using simple business day logic.

        Args:
            txn_date: Transaction date
            settlement_days: Number of business days to add

        Returns:
            Settlement date in YYYY-MM-DD format
        """
        current_date = txn_date
        days_added = 0

        while days_added < settlement_days:
            current_date = current_date + pd.DateOffset(days=1)
            if current_date.weekday() < WEEKDAYS_IN_WEEK:
                days_added += 1

        return current_date.strftime("%Y-%m-%d")

    def get_calendar_schedule(
        self,
        currency: Currency,
        start_date: pd.Timestamp,
        end_date: pd.Timestamp,
    ) -> pd.DatetimeIndex:
        """Get or create calendar schedule for date range.

        Args:
            currency: Currency to get calendar for
            start_date: Start date for schedule
            end_date: End date for schedule

        Returns:
            DatetimeIndex of valid trading days
        """
        # Check if we have a cached schedule that covers our range
        if currency in self.calendar_schedules:
            existing_schedule = self.calendar_schedules[currency]
            if (
                len(existing_schedule) > 0
                and existing_schedule[0].tz_localize(TORONTO_TZ) <= start_date
                and existing_schedule[-1].tz_localize(TORONTO_TZ) >= end_date
            ):
                return existing_schedule

        calendar: mcal.MarketCalendar | None = self._calendars.get(currency)
        if calendar is None:  # pragma: no cover
            return pd.DatetimeIndex([])

        buffer_start: pd.Timestamp = start_date - pd.DateOffset(days=10)
        buffer_end: pd.Timestamp = end_date + pd.DateOffset(days=30)

        schedule: pd.DataFrame = calendar.schedule(
            start_date=buffer_start,
            end_date=buffer_end,
        )
        valid_days: pd.DatetimeIndex = pd.DatetimeIndex(schedule.index)

        # Cache the schedule
        self.calendar_schedules[currency] = valid_days
        return valid_days

    def _get_settlement_days(
        self,
        action: Action,
        currency: Currency,
        txn_date: date,
    ) -> tuple[int, int]:
        """Get number of settlement days for a transaction.

        Args:
            action: Transaction action
            currency: Transaction currency
            txn_date: Transaction date

        Returns:
            Tuple of (settlement days, 0/1 for calculated or not)
        """
        if action in SAME_DAY_SETTLE_ACTIONS:
            return 0, 0

        if action in BUSINESS_DAY_SETTLE_ACTIONS:
            # Check if T+1 is effective for this currency and date
            effective_date = T_PLUS_1_EFFECTIVE_DATES.get(currency)
            if effective_date and txn_date >= effective_date:
                return 1, 1  # T+1 settlement
            return 2, 1  # T+2 settlement

        return 0, 0  # pragma: no cover


# Global instance for use throughout the application
settlement_calculator = SettlementCalculator()
