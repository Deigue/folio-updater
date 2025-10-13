"""Settlement date calculation utilities."""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from utils.constants import TORONTO_TZ, Action, Column, Currency
from utils.logging_setup import get_import_logger

if TYPE_CHECKING:
    from datetime import date

    import pandas_market_calendars as mcal

import_logger = get_import_logger()

WEEKDAYS_IN_WEEK: int = 5
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
        ].astype(str).str.match(r"^\d{4}-\d{2}-\d{2}$")
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

        for currency in df_copy[needs_calculation][Column.Txn.CURRENCY].unique():
            currency_mask = needs_calculation & (
                df_copy[Column.Txn.CURRENCY] == currency
            )
            if currency_mask.any():
                schedule = self.calendar_schedules.get(currency, pd.DatetimeIndex([]))
                currency_indices = df_copy[currency_mask].index
                for idx in currency_indices:
                    self._process_settlement_for_row_optimized(
                        df_copy,
                        idx,
                        currency,
                        schedule,
                    )

        return df_copy

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
                and existing_schedule[0] <= start_date
                and existing_schedule[-1] >= end_date
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

    def _process_settlement_for_row_optimized(
        self,
        df: pd.DataFrame,
        idx: int,
        currency: Currency,
        schedule: pd.DatetimeIndex,
    ) -> None:
        """Process settlement date for a single row using pre-loaded calendar.

        Args:
            df: DataFrame being processed
            idx: Row index to process
            currency: Currency for the transaction
            schedule: Pre-loaded calendar schedule
        """
        # Check if we already have a valid settlement date
        existing_settle_date = df.loc[idx, Column.Txn.SETTLE_DATE]
        if pd.notna(existing_settle_date) and self._is_valid_date(
            str(existing_settle_date),
        ):
            df.loc[idx, Column.Txn.SETTLE_CALCULATED] = 0
            return

        # Get transaction data (already validated by formatter)
        txn_date_str = df.loc[idx, Column.Txn.TXN_DATE]
        action_str = df.loc[idx, Column.Txn.ACTION]

        # Convert to objects we need (no try/except needed - data is pre-validated)
        txn_date = self._get_date_from_string(str(txn_date_str))
        action = Action(action_str)
        settlement_days = self._get_settlement_days(action, currency, txn_date)

        if settlement_days == 0:
            settle_date = txn_date_str
        else:
            # Use pre-loaded schedule for business day calculation
            settle_date = self._calculate_with_preloaded_schedule(
                txn_date,
                settlement_days,
                schedule,
            )

        df.loc[idx, Column.Txn.SETTLE_DATE] = settle_date
        df.loc[idx, Column.Txn.SETTLE_CALCULATED] = 1

    def _calculate_with_preloaded_schedule(
        self,
        txn_date: date,
        settlement_days: int,
        schedule: pd.DatetimeIndex,
    ) -> str:
        """Calculate settlement using pre-loaded calendar schedule.

        Args:
            txn_date: Transaction date
            settlement_days: Number of business days to add
            schedule: Pre-loaded calendar schedule

        Returns:
            Settlement date in YYYY-MM-DD format
        """
        if len(schedule) == 0:  # pragma: no cover
            return self.calculate_simple_business_days(txn_date, settlement_days)

        start_ts = pd.Timestamp(txn_date)
        valid_days_after_txn = schedule[schedule > start_ts]

        if len(valid_days_after_txn) >= settlement_days:
            settle_date = valid_days_after_txn[settlement_days - 1].date()
            return settle_date.strftime("%Y-%m-%d")

        # Fallback if not enough valid days found
        return self.calculate_simple_business_days(
            txn_date,
            settlement_days,
        )  # pragma: no cover

    def _get_settlement_days(
        self,
        action: Action,
        currency: Currency,
        txn_date: date,
    ) -> int:
        """Get number of settlement days for a transaction.

        Args:
            action: Transaction action
            currency: Transaction currency
            txn_date: Transaction date

        Returns:
            Number of business days for settlement (0 for same-day)
        """
        if action in SAME_DAY_SETTLE_ACTIONS:
            return 0

        if action in BUSINESS_DAY_SETTLE_ACTIONS:
            # Check if T+1 is effective for this currency and date
            effective_date = T_PLUS_1_EFFECTIVE_DATES.get(currency)
            if effective_date and txn_date >= effective_date:
                return 1  # T+1 settlement
            return 2  # T+2 settlement

        return 0  # pragma: no cover

    def _is_valid_date(self, date_str: str) -> bool:
        """Check if a string represents a valid date in YYYY-MM-DD format.

        Args:
            date_str: Date string to validate

        Returns:
            True if valid, False otherwise
        """
        return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", date_str))

    def _get_date_from_string(self, date_str: str) -> date:
        """Convert a date string in YYYY-MM-DD format to a date object."""
        return (
            datetime.strptime(
                str(date_str),
                "%Y-%m-%d",
            )
            .replace(tzinfo=TORONTO_TZ)
            .date()
        )


# Global instance for use throughout the application
settlement_calculator = SettlementCalculator()
