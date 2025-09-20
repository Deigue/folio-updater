"""Data formatting and validation utilities for database operations."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import ClassVar

import pandas as pd

from app.app_context import get_config
from db.utils import format_transaction_summary
from utils.constants import TORONTO_TZ, Action, Column, Currency
from utils.logging_setup import get_import_logger
from utils.optional_fields import FieldType

logger = logging.getLogger(__name__)
import_logger = get_import_logger()
actions: list[str] = [action.value for action in Action]
currencies: set[str] = {currency.value for currency in Currency}
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
        Action.FXT: {
            "required_fields": [
                Column.Txn.AMOUNT.value,
                Column.Txn.ACCOUNT.value,
                Column.Txn.CURRENCY.value,
            ],
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

    CURRENCY_MAP: ClassVar[dict[str, str]] = {
        "US$": "USD",
        "C$": "CAD",
        "CAD$": "CAD",
        "CANADIAN": "CAD",
    }

    ACTION_MAP: ClassVar[dict[str, str]] = {
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

    def __init__(self, df: pd.DataFrame) -> None:
        """Initialize formatter with transaction data.

        Args:
            df: DataFrame with transaction data
        """
        self.original_df = df
        self.formatted_df = df.copy()
        self.exclusions: list[int] = []
        self.rejection_reasons: dict[int, list[str]] = {}
        self.config = get_config()

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

        formatter = TransactionFormatter(df)
        return formatter._process()

    def _process(self) -> pd.DataFrame:
        """Process the transaction data through all formatting steps."""
        self._format_dates()
        self._format_actions()
        self._format_currencies()
        self._format_tickers()
        self._validation_action_based_columns()
        self._format_optional_columns()

        return self._finalize_exclusions()

    def _finalize_exclusions(self) -> pd.DataFrame:
        """Remove excluded rows and log rejection details."""
        if self.exclusions:
            excluded_indices = set(self.exclusions)
            self.formatted_df = self.formatted_df[
                ~self.formatted_df.index.isin(excluded_indices)
            ]

            excluded_count = len(excluded_indices)
            import_logger.warning(
                "Excluded %d transactions due to invalid formatting.",
                excluded_count,
            )

            for idx in sorted(excluded_indices):
                if idx < len(self.original_df):  # pragma: no branch
                    row = self.original_df.iloc[idx]
                    reasons = self.rejection_reasons.get(idx, ["Unknown reason"])
                    reason_str = ", ".join(reasons)
                    import_logger.warning(
                        "%d - %s (%s)",
                        idx,
                        format_transaction_summary(row),
                        reason_str,
                    )

        return self.formatted_df

    def _format_dates(self) -> None:
        """Format date columns to YYYY-MM-DD format.

        Handles both required transaction date and optional date fields.
        """
        # Format required transaction date
        if Column.Txn.TXN_DATE.value in self.formatted_df.columns:  # pragma: no cover
            self._format_date_column(Column.Txn.TXN_DATE.value, required=True)

        # Format optional date fields
        if self.config.optional_fields:
            for column in self.formatted_df.columns:
                optional_field = self.config.optional_fields.get_field(column)
                if optional_field and optional_field.field_type == FieldType.DATE:
                    self._format_date_column(column, required=False)

    def _format_date_column(self, column: str, *, required: bool) -> None:
        """Format a specific date column.

        Args:
            column: Column name to format
            required: Whether this column is required (affects exclusion behavior)
        """
        for idx in self.formatted_df.index:
            if idx in self.exclusions:  # pragma: no cover
                continue
            self._process_date_value(idx, column, required=required)

    def _process_date_value(self, idx: int, column: str, *, required: bool) -> None:
        """Process a single date value for formatting.

        Args:
            idx: Row index
            column: Column name
            required: Whether the column is required
        """
        value = self.formatted_df.loc[idx, column]
        if pd.isna(value):
            if required:
                self.exclusions.append(idx)
                reason = f"MISSING {column}"
                self.rejection_reasons.setdefault(idx, []).append(reason)
            else:
                self.formatted_df.loc[idx, column] = pd.NA
            return

        formatted_date: str | None = parse_date(str(value))
        if formatted_date is None:
            if required:
                self.exclusions.append(idx)
                reason = f"INVALID {column}"
                self.rejection_reasons.setdefault(idx, []).append(reason)
            else:
                import_logger.debug(
                    "%d - Invalid optional date field '%s': '%s'",
                    idx,
                    column,
                    value,
                )
        else:
            if formatted_date != str(value).strip():
                import_logger.debug(
                    AUTO_FORMAT_DEBUG,
                    idx,
                    column,
                    value,
                    formatted_date,
                )
            self.formatted_df.loc[idx, column] = formatted_date

    def _format_actions(self) -> None:
        """Format action columns to valid enum values.

        Handles both required transaction action and optional action fields.
        """
        # Format required transaction action
        if Column.Txn.ACTION.value in self.formatted_df.columns:  # pragma: no cover
            self._format_action_column(Column.Txn.ACTION.value, required=True)

        # Format optional action fields
        if self.config.optional_fields:
            for column in self.formatted_df.columns:
                optional_field = self.config.optional_fields.get_field(column)
                if optional_field and optional_field.field_type == FieldType.ACTION:
                    self._format_action_column(column, required=False)

    def _format_action_column(self, column: str, *, required: bool) -> None:
        """Format a specific action column.

        Args:
            column: Column name to format
            required: Whether this column is required (affects exclusion behavior)
        """
        for idx in self.formatted_df.index:
            if idx in self.exclusions:
                continue
            self._process_action_value(
                idx,
                column,
                required=required,
            )

    def _process_action_value(
        self,
        idx: int,
        column: str,
        *,
        required: bool,
    ) -> None:
        """Process a single action value for formatting and validation.

        Args:
            idx: Row index
            column: Column name
            value: Value to process
            required: Whether the column is required
            valid_actions: Set of valid action strings
            action_mapping: Mapping for action normalization
        """
        value = self.formatted_df.loc[idx, column]
        if pd.isna(value):
            if required:
                self.exclusions.append(idx)
                reason = f"MISSING {column}"
                self.rejection_reasons.setdefault(idx, []).append(reason)
            else:
                self.formatted_df.loc[idx, column] = pd.NA
            return

        action_str = str(value).strip().upper()
        normalized_action = self.ACTION_MAP.get(action_str, action_str)

        if normalized_action in actions:
            if normalized_action != action_str:
                import_logger.debug(
                    AUTO_FORMAT_DEBUG,
                    idx,
                    column,
                    value,
                    normalized_action,
                )
            self.formatted_df.loc[idx, column] = normalized_action
        elif required:
            self.exclusions.append(idx)
            reason = f"INVALID {column}"
            self.rejection_reasons.setdefault(idx, []).append(reason)
        else:
            import_logger.debug(
                "%d - Invalid optional action field '%s': '%s'",
                idx,
                column,
                value,
            )

    def _format_currencies(self) -> None:
        """Format currency columns to valid enum values.

        Handles both required transaction currency and optional currency fields.
        """
        # Format required transaction currency
        if Column.Txn.CURRENCY.value in self.formatted_df.columns:  # pragma: no cover
            self._format_currency_column(Column.Txn.CURRENCY.value, required=True)

        # Format optional currency fields
        if self.config.optional_fields:
            for column in self.formatted_df.columns:
                optional_field = self.config.optional_fields.get_field(column)
                if optional_field and optional_field.field_type == FieldType.CURRENCY:
                    self._format_currency_column(column, required=False)

    def _format_currency_column(self, column: str, *, required: bool) -> None:
        """Format a specific currency column.

        Args:
            column: Column name to format
            required: Whether this column is required (affects exclusion behavior)
        """
        for idx in self.formatted_df.index:
            if idx in self.exclusions:
                continue
            self._process_currency_value(
                idx,
                column,
                required=required,
            )

    def _process_currency_value(
        self,
        idx: int,
        column: str,
        *,
        required: bool,
    ) -> None:
        """Process a single currency value for formatting and validation.

        Args:
            idx: Row index
            column: Column name
            required: Whether the column is required
            valid_currencies: Set of valid currency strings
            currency_mapping: Mapping for currency normalization
        """
        value = self.formatted_df.loc[idx, column]
        if pd.isna(value):
            if required:
                self.exclusions.append(idx)
                reason = f"MISSING {column}"
                self.rejection_reasons.setdefault(idx, []).append(reason)
            else:
                self.formatted_df.loc[idx, column] = pd.NA
            return

        currency_str = str(value).strip().upper()
        normalized_currency = self.CURRENCY_MAP.get(currency_str, currency_str)

        if normalized_currency in currencies:
            if normalized_currency != currency_str:
                import_logger.debug(
                    AUTO_FORMAT_DEBUG,
                    idx,
                    column,
                    value,
                    normalized_currency,
                )
            self.formatted_df.loc[idx, column] = normalized_currency
        elif required:
            self.exclusions.append(idx)
            reason = f"INVALID {column}"
            self.rejection_reasons.setdefault(idx, []).append(reason)
        else:
            import_logger.debug(
                "%d - Invalid optional currency field '%s': '%s'",
                idx,
                column,
                value,
            )

    def _format_tickers(self) -> None:
        """Format ticker column to uppercase and trim whitespace.

        Ticker is optional - if empty/null, it will be kept as null.
        If present, it must be uppercase and contain only valid characters.
        """
        if Column.Txn.TICKER.value not in self.formatted_df.columns:  # pragma: no cover
            return

        ticker_col = Column.Txn.TICKER.value

        for idx in self.formatted_df.index:
            if idx in self.exclusions:
                continue

            value = self.formatted_df.loc[idx, ticker_col]
            if pd.isna(value) or str(value).strip() == "":
                self.formatted_df.loc[idx, ticker_col] = pd.NA
                continue

            ticker_str = str(value).strip().upper()
            if not re.match(r"^[A-Z0-9.-]+$", ticker_str) or len(ticker_str) == 0:
                self.exclusions.append(idx)
                reason = f"INVALID {ticker_col}"
                self.rejection_reasons.setdefault(idx, []).append(reason)
            else:
                if ticker_str != str(value).strip():
                    import_logger.debug(
                        AUTO_FORMAT_DEBUG,
                        idx,
                        ticker_col,
                        value,
                        ticker_str,
                    )
                self.formatted_df.loc[idx, ticker_col] = ticker_str

    def _validation_action_based_columns(self) -> None:
        """Validate fields based on action-specific rules and format numeric fields.

        Handles both required numeric fields and optional fields of all types.
        """
        # Convert all numeric columns to object dtype once to prevent warnings
        numeric_fields = [
            Column.Txn.AMOUNT.value,
            Column.Txn.PRICE.value,
            Column.Txn.UNITS.value,
        ]

        for field in numeric_fields:
            if field in self.formatted_df.columns:  # pragma: no branch
                self.formatted_df[field] = self.formatted_df[field].astype("object")

        for row in self.formatted_df.index:
            if row in self.exclusions:
                continue

            rules = self._get_validation_rules_for_row(row)
            required_fields = rules["required_fields"]
            self._validate_required_for_row(row, required_fields)

    def _get_validation_rules_for_row(self, idx: int) -> dict[str, list[str]]:
        """Get validation rules for a specific row based on its action."""
        action_col = Column.Txn.ACTION.value
        if action_col not in self.formatted_df.columns:  # pragma: no cover
            return ActionValidationRules.DEFAULT

        action_value = self.formatted_df.loc[idx, action_col]
        if pd.isna(action_value):  # pragma: no cover
            return ActionValidationRules.DEFAULT

        return ActionValidationRules.get_rules_for_action(str(action_value))

    def _validate_required_for_row(
        self,
        row: int,
        required_fields: list[str],
    ) -> None:
        """Validate all required fields for a row (both numeric and non-numeric)."""
        # First validate numeric fields
        numeric_fields = [
            Column.Txn.AMOUNT.value,
            Column.Txn.PRICE.value,
            Column.Txn.UNITS.value,
        ]

        for field in numeric_fields:
            if field not in self.formatted_df.columns:  # pragma: no cover
                continue

            is_required = field in required_fields
            success = self._process_numeric_value(row, field, required=is_required)

            if is_required and not success:
                return  # Row already excluded, no need to check other fields

        # Now validate non-numeric required fields
        non_numeric_required_fields = [
            field
            for field in required_fields
            if field not in numeric_fields and field in self.formatted_df.columns
        ]

        for field in non_numeric_required_fields:
            value = self.formatted_df.loc[row, field]
            if pd.isna(value) or (isinstance(value, str) and value.strip() == ""):
                self.exclusions.append(row)
                reason = f"MISSING {field}"
                self.rejection_reasons.setdefault(row, []).append(reason)

    def _format_optional_columns(self) -> None:
        """Format all optional fields based on their configured types."""
        # Exit early if no optional fields are configured
        if not self.config.optional_fields:
            return

        # Convert all optional numeric columns to object dtype to prevent warnings
        for column in self.formatted_df.columns:
            optional_field = self.config.optional_fields.get_field(column)
            if optional_field and optional_field.field_type == FieldType.NUMERIC:
                self.formatted_df[column] = self.formatted_df[column].astype("object")

        for column in self.formatted_df.columns:
            optional_field = self.config.optional_fields.get_field(column)
            if optional_field is None:
                continue

            field_type = optional_field.field_type
            if field_type == FieldType.NUMERIC:
                self._format_numeric_column(column, required=False)
            elif field_type == FieldType.STRING:
                self._format_string_column(column)

    def _format_string_column(self, column: str) -> None:
        """Format a string column by trimming whitespace.

        Args:
            column: Column name to format
        """
        for idx in self.formatted_df.index:
            if idx in self.exclusions:  # pragma: no cover
                continue

            value = self.formatted_df.loc[idx, column]
            if pd.isna(value) or str(value).strip() == "":
                self.formatted_df.loc[idx, column] = pd.NA
            else:
                string_value = str(value).strip()
                if string_value != str(value):
                    import_logger.debug(
                        AUTO_FORMAT_DEBUG,
                        idx,
                        column,
                        value,
                        string_value,
                    )
                self.formatted_df.loc[idx, column] = string_value

    def _format_numeric_column(self, column: str, *, required: bool) -> None:
        """Format a specific numeric column.

        Args:
            column: Column name to format
            required: Whether this column is required (affects exclusion behavior)
        """
        for idx in self.formatted_df.index:
            if idx in self.exclusions:  # pragma: no cover
                continue
            self._process_numeric_value(idx, column, required=required)

    def _process_numeric_value(
        self,
        idx: int,
        field: str,
        *,
        required: bool,
    ) -> bool:
        """Format a numeric field value.

        Returns:
            True if successful or field is optional, False if required field failed
        """
        value = self.formatted_df.loc[idx, field]
        if pd.isna(value) or str(value).strip() == "":
            if required:
                self.exclusions.append(idx)
                reason = f"MISSING {field}"
                self.rejection_reasons.setdefault(idx, []).append(reason)
                return False
            self.formatted_df.loc[idx, field] = pd.NA
            return True

        clean_value = str(value).strip().replace("$", "").replace(",", "")
        try:
            decimal_value = Decimal(clean_value)
            formatted_value = format(decimal_value, "f")  # Always plain decimal
        except (ValueError, TypeError, InvalidOperation):
            if required:
                self.exclusions.append(idx)
                reason = f"INVALID {field}"
                self.rejection_reasons.setdefault(idx, []).append(reason)
                return False

            # For optional fields, just create a log entry
            import_logger.debug(
                "%d - Invalid optional numeric field '%s': '%s'",
                idx,
                field,
                value,
            )
            return True
        else:
            self.formatted_df.loc[idx, field] = formatted_value
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
                tzinfo=TORONTO_TZ,
            )
            return parsed_date.strftime("%Y-%m-%d")
        except ValueError:  # noqa: PERF203
            continue

    return None
