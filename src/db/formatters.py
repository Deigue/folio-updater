"""Data formatting and validation utilities for database operations."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from db.utils import format_transaction_summary
from utils.constants import Action, Column, Currency
from utils.logging_setup import get_import_logger

logger = logging.getLogger(__name__)
import_logger = get_import_logger()

AUTO_FORMAT_DEBUG: str = "%d - Auto-formatted %s: '%s' -> '%s'"


class TransactionFormatter:
    """Formatter for transaction data before database insertion."""

    @staticmethod
    def format_and_validate(df: pd.DataFrame) -> pd.DataFrame:
        """Format and validate transaction data, removing invalid rows.

        Args:
            df: DataFrame with transaction data

        Returns:
            DataFrame with formatted data and invalid rows removed
        """
        if df.empty:  # pragma: no cover
            return df

        formatted_df = df.copy()
        exclusions: list[int] = []
        rejection_reasons: dict[int, list[str]] = {}

        formatted_df = TransactionFormatter._format_date(
            formatted_df,
            exclusions,
            rejection_reasons,
        )
        formatted_df = TransactionFormatter._format_action(
            formatted_df,
            exclusions,
            rejection_reasons,
        )
        formatted_df = TransactionFormatter._format_currency(
            formatted_df,
            exclusions,
            rejection_reasons,
        )
        formatted_df = TransactionFormatter._format_ticker(
            formatted_df,
            exclusions,
            rejection_reasons,
        )
        formatted_df = TransactionFormatter._format_numeric_fields(
            formatted_df,
            exclusions,
            rejection_reasons,
        )

        if exclusions:
            excluded_indices = set(exclusions)
            formatted_df = formatted_df[~formatted_df.index.isin(excluded_indices)]

            excluded_count = len(excluded_indices)
            import_logger.warning(
                "Excluded %d transactions due to invalid formatting.",
                excluded_count,
            )

            for idx in sorted(excluded_indices):
                if idx < len(df):  # pragma: no branch
                    row = df.iloc[idx]
                    reasons = rejection_reasons.get(idx, ["Unknown reason"])
                    reason_str = ", ".join(reasons)
                    import_logger.warning(
                        "%d - %s (%s)",
                        idx,
                        format_transaction_summary(row),
                        reason_str,
                    )

        return formatted_df

    @staticmethod
    def _format_date(
        df: pd.DataFrame,
        exclusions: list[int],
        rejection_reasons: dict[int, list[str]],
    ) -> pd.DataFrame:
        """Format date column to YYYY-MM-DD format."""
        if Column.Txn.TXN_DATE.value not in df.columns:  # pragma: no cover
            return df

        date_col = Column.Txn.TXN_DATE.value
        for idx in df.index:
            value = df.loc[idx, date_col]
            if pd.isna(value):
                exclusions.append(idx)
                reason = f"MISSING {date_col}"
                rejection_reasons.setdefault(idx, []).append(reason)
                continue

            formatted_date = parse_date(str(value))
            if formatted_date is None:
                exclusions.append(idx)
                reason = f"INVALID {date_col}"
                rejection_reasons.setdefault(idx, []).append(reason)
            else:
                if formatted_date != str(value).strip():
                    import_logger.debug(
                        AUTO_FORMAT_DEBUG,
                        idx,
                        date_col,
                        value,
                        formatted_date,
                    )
                df.loc[idx, date_col] = formatted_date

        return df

    @staticmethod
    def _format_action(
        df: pd.DataFrame,
        exclusions: list[int],
        rejection_reasons: dict[int, list[str]],
    ) -> pd.DataFrame:
        """Format action column to valid enum values."""
        if Column.Txn.ACTION.value not in df.columns:  # pragma: no cover
            return df

        action_col = Column.Txn.ACTION.value
        valid_actions = {action.value.upper() for action in Action}

        # Common action mappings to attempt normalization
        action_mapping = {
            "B": "BUY",
            "PURCHASE": "BUY",
            "BOUGHT": "BUY",
            "S": "SELL",
            "SOLD": "SELL",
            "SALE": "SELL",
            "DIV": "DIVIDEND",
            "DIVIDEND": "DIVIDEND",
            "BORROW": "BRW",
            "BORROWING": "BRW",
            "CONTRIB": "CONTRIBUTION",
            "DEPOSIT": "CONTRIBUTION",
            "FEE": "FCH",
            "FEES": "FCH",
            "INTEREST": "FCH",
            "RSU": "FCH",
            "FOREX": "FXT",
            "FX": "FXT",
            "CURRENCY": "FXT",
            "RETURN_OF_CAPITAL": "ROC",
            "STOCK_SPLIT": "SPLIT",
            "WITHDRAW": "WITHDRAWAL",
            "CASH_OUT": "WITHDRAWAL",
        }

        for idx in df.index:
            value = df.loc[idx, action_col]
            if pd.isna(value):
                exclusions.append(idx)
                reason = f"MISSING {action_col}"
                rejection_reasons.setdefault(idx, []).append(reason)
                continue

            action_str = str(value).strip().upper()
            normalized_action = action_mapping.get(action_str, action_str)

            if normalized_action in valid_actions:
                if normalized_action != action_str:
                    import_logger.debug(
                        AUTO_FORMAT_DEBUG,
                        idx,
                        action_col,
                        value,
                        normalized_action,
                    )
                df.loc[idx, action_col] = normalized_action
            else:
                exclusions.append(idx)
                reason = f"INVALID {action_col}"
                rejection_reasons.setdefault(idx, []).append(reason)

        return df

    @staticmethod
    def _format_currency(
        df: pd.DataFrame,
        exclusions: list[int],
        rejection_reasons: dict[int, list[str]],
    ) -> pd.DataFrame:
        """Format currency column to valid enum values."""
        if Column.Txn.CURRENCY.value not in df.columns:  # pragma: no cover
            return df

        currency_col = Column.Txn.CURRENCY.value
        valid_currencies = {currency.value.upper() for currency in Currency}
        currency_mapping = {
            "US$": "USD",
            "C$": "CAD",
            "CAD$": "CAD",
            "CANADIAN": "CAD",
        }

        for idx in df.index:
            value = df.loc[idx, currency_col]
            if pd.isna(value):
                exclusions.append(idx)
                reason = f"MISSING {currency_col}"
                rejection_reasons.setdefault(idx, []).append(reason)
                continue

            currency_str = str(value).strip().upper()
            normalized_currency = currency_mapping.get(currency_str, currency_str)

            if normalized_currency in valid_currencies:
                if normalized_currency != currency_str:
                    import_logger.debug(
                        AUTO_FORMAT_DEBUG,
                        idx,
                        currency_col,
                        value,
                        normalized_currency,
                    )
                df.loc[idx, currency_col] = normalized_currency
            else:
                exclusions.append(idx)
                reason = f"INVALID {currency_col}"
                rejection_reasons.setdefault(idx, []).append(reason)

        return df

    @staticmethod
    def _format_ticker(
        df: pd.DataFrame,
        exclusions: list[int],
        rejection_reasons: dict[int, list[str]],
    ) -> pd.DataFrame:
        """Format ticker column to uppercase and trim whitespace.

        Ticker is optional - if empty/null, it will be kept as null.
        If present, it must be uppercase and contain only valid characters.
        """
        if Column.Txn.TICKER.value not in df.columns:  # pragma: no cover
            return df

        ticker_col = Column.Txn.TICKER.value

        for idx in df.index:
            value = df.loc[idx, ticker_col]
            if pd.isna(value) or str(value).strip() == "":
                df.loc[idx, ticker_col] = pd.NA
                continue

            ticker_str = str(value).strip().upper()
            if not re.match(r"^[A-Z0-9.-]+$", ticker_str) or len(ticker_str) == 0:
                exclusions.append(idx)
                reason = f"INVALID {ticker_col}"
                rejection_reasons.setdefault(idx, []).append(reason)
            else:
                if ticker_str != str(value).strip():
                    import_logger.debug(
                        AUTO_FORMAT_DEBUG,
                        idx,
                        ticker_col,
                        value,
                        ticker_str,
                    )
                df.loc[idx, ticker_col] = ticker_str

        return df

    @staticmethod
    def _format_numeric_fields(
        df: pd.DataFrame,
        exclusions: list[int],
        rejection_reasons: dict[int, list[str]],
    ) -> pd.DataFrame:
        """Format known numeric fields (Amount, Price, Units) to REAL type."""
        numeric_fields = [
            Column.Txn.AMOUNT.value,
            Column.Txn.PRICE.value,
            Column.Txn.UNITS.value,
        ]

        def try_format_float(idx: int, field: str, value: Any) -> None:  # noqa: ANN401
            if pd.isna(value):
                exclusions.append(idx)
                reason = f"MISSING {field}"
                rejection_reasons.setdefault(idx, []).append(reason)
                return
            clean_value = str(value).strip().replace("$", "").replace(",", "")
            if re.match(r"^-?\d+(\.\d+)?$", clean_value):
                if clean_value != str(value).strip():
                    import_logger.debug(
                        AUTO_FORMAT_DEBUG,
                        idx,
                        field,
                        value,
                        clean_value,
                    )
                df.loc[idx, field] = float(clean_value)
            else:
                exclusions.append(idx)
                reason = f"INVALID {field}"
                rejection_reasons.setdefault(idx, []).append(reason)

        for field in numeric_fields:
            if field not in df.columns:  # pragma: no cover
                continue
            for idx in df.index:
                value = df.loc[idx, field]
                try_format_float(idx, field, value)

        return df


def parse_date(date_str: str) -> str | None:
    """Parse various date formats to YYYY-MM-DD.

    Args:
        date_str: Date string in various formats

    Returns:
        Date in YYYY-MM-DD format or None if invalid
    """
    date_str = str(date_str).strip()

    # Already in correct format
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return date_str

    # Handle ISO 8601 formats with timezone and milliseconds
    # e.g., "2025-02-05T20:29:41.785270Z" or "2025-02-07T00:00:00Z"
    iso_pattern = (
        r"^(\d{4}-\d{2}-\d{2})T\d{2}:\d{2}:\d{2}"
        r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
    )
    iso_match = re.match(iso_pattern, date_str)
    if iso_match:
        return iso_match.group(1)

    # Handle datetime formats with space separator
    # e.g., "2025-02-07 00:00:00"
    datetime_pattern = r"^(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?$"
    datetime_match = re.match(datetime_pattern, date_str)
    if datetime_match:
        return datetime_match.group(1)

    # Common date formats to try
    date_formats = [
        "%Y-%m-%d",  # 2023-01-15
        "%m/%d/%Y",  # 01/15/2023
        "%d/%m/%Y",  # 15/01/2023
        "%m-%d-%Y",  # 01-15-2023
        "%d-%m-%Y",  # 15-01-2023
        "%Y/%m/%d",  # 2023/01/15
        "%d.%m.%Y",  # 15.01.2023
        "%m.%d.%Y",  # 01.15.2023
        "%B %d, %Y",  # January 15, 2023
        "%b %d, %Y",  # Jan 15, 2023
        "%d %B %Y",  # 15 January 2023
        "%d %b %Y",  # 15 Jan 2023
    ]

    for fmt in date_formats:  # pragma: no cover
        try:
            parsed_date: datetime = datetime.strptime(date_str, fmt).replace(
                tzinfo=timezone.utc,
            )
            return parsed_date.strftime("%Y-%m-%d")
        except ValueError:  # noqa: PERF203
            continue

    return None
