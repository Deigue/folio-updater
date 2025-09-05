"""Data formatting and validation utilities for database operations."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import pandas as pd

from db.utils import format_transaction_summary
from utils.constants import Action, Column, Currency
from utils.logging_setup import get_import_logger

logger = logging.getLogger(__name__)
import_logger = get_import_logger()


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
        if df.empty:
            return df

        formatted_df = df.copy()
        exclusions: list[int] = []

        formatted_df = TransactionFormatter._format_date(formatted_df, exclusions)
        formatted_df = TransactionFormatter._format_action(formatted_df, exclusions)
        formatted_df = TransactionFormatter._format_currency(formatted_df, exclusions)
        formatted_df = TransactionFormatter._format_ticker(formatted_df, exclusions)
        formatted_df = TransactionFormatter._format_numeric_fields(
            formatted_df,
            exclusions,
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
                if idx < len(df):
                    row = df.iloc[idx]
                    import_logger.warning(
                        "%d - %s",
                        idx,
                        format_transaction_summary(row),
                    )

        return formatted_df

    @staticmethod
    def _format_date(df: pd.DataFrame, exclusions: list[int]) -> pd.DataFrame:
        """Format date column to YYYY-MM-DD format."""
        if Column.Txn.TXN_DATE.value not in df.columns:
            return df

        date_col = Column.Txn.TXN_DATE.value
        for idx in df.index:
            value = df.loc[idx, date_col]
            if pd.isna(value):
                exclusions.append(idx)
                msg = f"{date_col} is missing at index {idx}"
                import_logger.debug(msg)
                continue

            formatted_date = parse_date(str(value))
            if formatted_date is None:
                exclusions.append(idx)
                msg = f"Invalid date format at index {idx}: {value}"
                import_logger.debug(msg)
            else:
                df.loc[idx, date_col] = formatted_date

        return df

    @staticmethod
    def _format_action(df: pd.DataFrame, exclusions: list[int]) -> pd.DataFrame:
        """Format action column to valid enum values."""
        if Column.Txn.ACTION.value not in df.columns:
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
        }

        for idx in df.index:
            value = df.loc[idx, action_col]
            if pd.isna(value):
                exclusions.append(idx)
                msg = f"{action_col} is missing at index {idx}"
                import_logger.debug(msg)
                continue

            action_str = str(value).strip().upper()
            normalized_action = action_mapping.get(action_str, action_str)

            if normalized_action in valid_actions:
                df.loc[idx, action_col] = normalized_action
            else:
                exclusions.append(idx)
                msg = f"Invalid action at index {idx}: {value}"
                import_logger.debug(msg)

        return df

    @staticmethod
    def _format_currency(df: pd.DataFrame, exclusions: list[int]) -> pd.DataFrame:
        """Format currency column to valid enum values."""
        if Column.Txn.CURRENCY.value not in df.columns:
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
                msg = f"{currency_col} is missing at index {idx}"
                import_logger.debug(msg)
                continue

            currency_str = str(value).strip().upper()
            normalized_currency = currency_mapping.get(currency_str, currency_str)

            if normalized_currency in valid_currencies:
                df.loc[idx, currency_col] = normalized_currency
            else:
                exclusions.append(idx)
                msg = f"Invalid currency at index {idx}: {value}"
                import_logger.debug(msg)

        return df

    @staticmethod
    def _format_ticker(df: pd.DataFrame, exclusions: list[int]) -> pd.DataFrame:
        """Format ticker column to uppercase and trim whitespace.

        Ticker is optional - if empty/null, it will be kept as null.
        If present, it must be uppercase and contain only valid characters.
        """
        if Column.Txn.TICKER.value not in df.columns:
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
                msg = f"Invalid ticker at index {idx}: {value}"
                import_logger.debug(msg)
            else:
                df.loc[idx, ticker_col] = ticker_str

        return df

    @staticmethod
    def _format_numeric_fields(df: pd.DataFrame, exclusions: list[int]) -> pd.DataFrame:
        """Format known numeric fields (Amount, Price, Units) to REAL type."""
        numeric_fields = [
            Column.Txn.AMOUNT.value,
            Column.Txn.PRICE.value,
            Column.Txn.UNITS.value,
        ]

        for field in numeric_fields:
            if field not in df.columns:
                continue

            for idx in df.index:
                value = df.loc[idx, field]
                if pd.isna(value):
                    exclusions.append(idx)
                    msg = f"{field} is missing at index {idx}"
                    import_logger.debug(msg)
                    continue

                # Remove currency symbols and commas
                clean_value = str(value).strip().replace("$", "").replace(",", "")
                # Check if clean_value is a valid float using regex
                if re.match(r"^-?\d+(\.\d+)?$", clean_value):
                    df.loc[idx, field] = float(clean_value)
                else:
                    exclusions.append(idx)
                    msg = f"Invalid numeric value for {field} at index {idx}: {value}"
                    import_logger.debug(msg)

        return df


@staticmethod
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
