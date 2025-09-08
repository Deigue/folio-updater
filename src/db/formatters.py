"""Data formatting and validation utilities for database operations."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, ClassVar

import pandas as pd

from db.utils import format_transaction_summary
from utils.constants import Action, Column, Currency
from utils.logging_setup import get_import_logger

logger = logging.getLogger(__name__)
import_logger = get_import_logger()

AUTO_FORMAT_DEBUG: str = "%d - Auto-formatted %s: '%s' -> '%s'"


class ActionValidationRules:
    """Defines validation rules for different transaction actions."""

    # Action-specific validation rules
    RULES: ClassVar[dict[Action, dict[str, list[str]]]] = {
        Action.CONTRIBUTION: {
            "required_fields": [Column.Txn.AMOUNT.value, Column.Txn.ACCOUNT.value],
            "optional_fields": [
                Column.Txn.PRICE.value,
                Column.Txn.UNITS.value,
                Column.Txn.TICKER.value,
            ],
        },
        Action.DIVIDEND: {
            "required_fields": [
                Column.Txn.AMOUNT.value,
                Column.Txn.ACCOUNT.value,
                Column.Txn.TICKER.value,
            ],
            "optional_fields": [
                Column.Txn.PRICE.value,
                Column.Txn.UNITS.value,
            ],
        },
        Action.FCH: {
            "required_fields": [Column.Txn.AMOUNT.value, Column.Txn.ACCOUNT.value],
            "optional_fields": [
                Column.Txn.PRICE.value,
                Column.Txn.UNITS.value,
                Column.Txn.TICKER.value,
            ],
        },
        Action.WITHDRAWAL: {
            "required_fields": [Column.Txn.AMOUNT.value, Column.Txn.ACCOUNT.value],
            "optional_fields": [
                Column.Txn.PRICE.value,
                Column.Txn.UNITS.value,
                Column.Txn.TICKER.value,
            ],
        },
        Action.ROC: {
            "required_fields": [
                Column.Txn.AMOUNT.value,
                Column.Txn.ACCOUNT.value,
                Column.Txn.TICKER.value,
            ],
            "optional_fields": [
                Column.Txn.PRICE.value,
                Column.Txn.UNITS.value,
            ],
        },
    }

    # Default rule for actions that require all fields (BUY, SELL, etc.)
    DEFAULT: ClassVar[dict[str, list[str]]] = {
        "required_fields": [
            Column.Txn.AMOUNT.value,
            Column.Txn.PRICE.value,
            Column.Txn.UNITS.value,
            Column.Txn.TICKER.value,
        ],
        "optional_fields": [],
    }

    @classmethod
    def get_rules_for_action(cls, action: str) -> dict[str, list[str]]:
        """Get validation rules for a specific action.

        Args:
            action: The action type as a string

        Returns:
            Dictionary with 'required_fields' and 'optional_fields' lists
        """
        try:
            action_enum = Action(action)
            return cls.RULES.get(action_enum, cls.DEFAULT)
        except ValueError:  # pragma: no cover
            # If action is not a valid enum, use default rules
            return cls.DEFAULT


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
        formatted_df = TransactionFormatter._validate_action_specific_fields(
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
    def _validate_action_specific_fields(
        df: pd.DataFrame,
        exclusions: list[int],
        rejection_reasons: dict[int, list[str]],
    ) -> pd.DataFrame:
        """Validate fields based on action-specific rules and format numeric fields."""
        # Validate each row based on its action
        for idx in df.index:
            if idx in exclusions:
                continue  # Skip already excluded rows

            # Get validation rules for this row's action
            rules = TransactionFormatter._get_validation_rules_for_row(df, idx)
            required_fields = rules["required_fields"]

            # Validate numeric fields based on action requirements
            TransactionFormatter._validate_row_numeric_fields(
                df,
                idx,
                required_fields,
                exclusions,
                rejection_reasons,
            )

        return df

    @staticmethod
    def _get_validation_rules_for_row(
        df: pd.DataFrame,
        idx: int,
    ) -> dict[str, list[str]]:
        """Get validation rules for a specific row based on its action."""
        action_col = Column.Txn.ACTION.value
        if action_col not in df.columns:  # pragma: no cover
            return ActionValidationRules.DEFAULT

        action_value = df.loc[idx, action_col]
        if pd.isna(action_value):  # pragma: no cover
            return ActionValidationRules.DEFAULT

        return ActionValidationRules.get_rules_for_action(str(action_value))

    @staticmethod
    def _validate_row_numeric_fields(
        df: pd.DataFrame,
        idx: int,
        required_fields: list[str],
        exclusions: list[int],
        rejection_reasons: dict[int, list[str]],
    ) -> None:
        """Validate numeric fields for a single row."""
        numeric_fields = [
            Column.Txn.AMOUNT.value,
            Column.Txn.PRICE.value,
            Column.Txn.UNITS.value,
        ]

        for field in numeric_fields:
            if field not in df.columns:  # pragma: no cover
                continue

            is_required = field in required_fields
            value = df.loc[idx, field]
            success = TransactionFormatter._format_numeric_field(
                df,
                idx,
                field,
                value,
                is_required,
                exclusions,
                rejection_reasons,
            )

            # If required field failed validation, mark row as excluded
            if not success and is_required:
                break

    @staticmethod
    def _format_numeric_field(  # noqa: PLR0913
        df: pd.DataFrame,
        idx: int,
        field: str,
        value: Any,  # noqa: ANN401
        is_required: bool,  # noqa: FBT001
        exclusions: list[int],
        rejection_reasons: dict[int, list[str]],
    ) -> bool:
        """Format a numeric field value.

        Returns:
            True if successful or field is optional, False if required field failed
        """
        if pd.isna(value) or str(value).strip() == "":
            if is_required:
                exclusions.append(idx)
                reason = f"MISSING {field}"
                rejection_reasons.setdefault(idx, []).append(reason)
                return False
            # Set optional fields to None/NULL
            df.loc[idx, field] = pd.NA
            return True

        clean_value = str(value).strip().replace("$", "").replace(",", "")
        try:
            float_value = float(clean_value)
        except (ValueError, TypeError):
            if is_required:
                exclusions.append(idx)
                reason = f"INVALID {field}"
                rejection_reasons.setdefault(idx, []).append(reason)
                return False
            # For optional fields, if invalid, set to None/NULL
            df.loc[idx, field] = pd.NA  # pragma: no cover
            return True  # pragma: no cover
        else:
            df.loc[idx, field] = float_value
            if clean_value != str(value).strip():
                import_logger.debug(
                    AUTO_FORMAT_DEBUG,
                    idx,
                    field,
                    value,
                    clean_value,
                )
            return True


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
