"""Data formatting and validation utilities for database operations."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import ClassVar

import pandas as pd

from app.app_context import get_config
from db.utils import format_transaction_summary
from utils.constants import TORONTO_TZ, Action, Column, Currency
from utils.logging_setup import get_import_logger
from utils.optional_fields import FieldType
from utils.settlement_calculator import settlement_calculator

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
            "required_fields": [Column.Txn.AMOUNT, Column.Txn.ACCOUNT],
            "optional_fields": [
                Column.Txn.PRICE,
                Column.Txn.UNITS,
                Column.Txn.TICKER,
            ],
        },
        Action.FXT: {
            "required_fields": [
                Column.Txn.AMOUNT,
                Column.Txn.ACCOUNT,
                Column.Txn.CURRENCY,
            ],
            "optional_fields": [
                Column.Txn.PRICE,
                Column.Txn.UNITS,
                Column.Txn.TICKER,
            ],
        },
        Action.DIVIDEND: {
            "required_fields": [
                Column.Txn.AMOUNT,
                Column.Txn.ACCOUNT,
                Column.Txn.TICKER,
            ],
            "optional_fields": [
                Column.Txn.PRICE,
                Column.Txn.UNITS,
            ],
        },
        Action.FCH: {
            "required_fields": [Column.Txn.AMOUNT, Column.Txn.ACCOUNT],
            "optional_fields": [
                Column.Txn.PRICE,
                Column.Txn.UNITS,
                Column.Txn.TICKER,
            ],
        },
        Action.WITHDRAWAL: {
            "required_fields": [Column.Txn.AMOUNT, Column.Txn.ACCOUNT],
            "optional_fields": [
                Column.Txn.PRICE,
                Column.Txn.UNITS,
                Column.Txn.TICKER,
            ],
        },
        Action.ROC: {
            "required_fields": [
                Column.Txn.AMOUNT,
                Column.Txn.ACCOUNT,
                Column.Txn.TICKER,
            ],
            "optional_fields": [
                Column.Txn.PRICE,
                Column.Txn.UNITS,
            ],
        },
        Action.SPLIT: {
            "required_fields": [
                Column.Txn.PRICE,
                Column.Txn.UNITS,
                Column.Txn.TICKER,
                Column.Txn.ACCOUNT,
            ],
            "optional_fields": [
                Column.Txn.AMOUNT,
                Column.Txn.FEE,
            ],
        },
    }

    # Default rule for actions that require all fields (BUY, SELL, etc.)
    DEFAULT: ClassVar[dict[str, list[str]]] = {
        "required_fields": [
            Column.Txn.AMOUNT,
            Column.Txn.PRICE,
            Column.Txn.UNITS,
            Column.Txn.TICKER,
            Column.Txn.CURRENCY,
        ],
        "optional_fields": [Column.Txn.FEE],
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
        except ValueError:
            # If action is not a valid enum, use default rules
            return cls.DEFAULT


def _to_decimal_format(value: str) -> str | None:
    """Try convert verified string to a plain decimal string."""
    try:
        decimal_value = Decimal(str(value))
        return format(decimal_value, "f")
    except (ValueError, TypeError, InvalidOperation):
        return None


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
        "DIVIDENDS": "DIVIDEND",
        "BORROW": "BRW",
        "BORROWING": "BRW",
        "CONTRIB": "CONTRIBUTION",
        "DEPOSIT": "CONTRIBUTION",
        "FEE": "FCH",
        "FEES": "FCH",
        "INTEREST": "FCH",
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
        self.excluded_df = pd.DataFrame()

    @staticmethod
    def format_and_validate(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Format and validate transaction data, removing invalid rows.

        Args:
            df: DataFrame with transaction data

        Returns:
            Tuple of (formatted_df, excluded_df)
        """
        if df.empty:  # pragma: no cover
            return df, pd.DataFrame()

        formatter = TransactionFormatter(df)
        formatter._process()
        return formatter.formatted_df, formatter.excluded_df

    def _process(self) -> None:
        """Process the transaction data through all formatting steps."""
        original_df = self.formatted_df.copy(deep=True)
        self._format_dates()
        self._format_actions()
        self._format_currencies()
        self._format_rule_columns()
        self._finalize_exclusions()
        self._log_formatting_changes(original_df)
        self._calculate_settlement_dates()

    def _finalize_exclusions(self) -> None:
        """Remove excluded rows and log rejection details."""
        if self.exclusions:
            excluded_indices = set(self.exclusions)
            self.excluded_df = self.original_df.loc[list(excluded_indices)].copy()
            if not self.excluded_df.empty:
                reasons_list = []
                for idx in self.excluded_df.index:
                    reasons = self.rejection_reasons.get(idx, ["Unknown"])
                    reasons_list.append("; ".join(reasons))
                self.excluded_df = self.excluded_df.assign(
                    Rejection_Reason=reasons_list,
                )

            self.formatted_df = self.formatted_df[
                ~self.formatted_df.index.isin(excluded_indices)
            ]

            if not import_logger.isEnabledFor(logging.WARNING):  # pragma: no cover
                return

            excluded_count = len(excluded_indices)
            import_logger.warning(
                "EXCLUDE %d transactions (invalid formatting)",
                excluded_count,
            )

            for idx in sorted(excluded_indices):
                if idx < len(self.original_df):
                    row = self.original_df.iloc[idx]
                    reasons = self.rejection_reasons.get(idx, ["Unknown reason"])
                    reason_str = ", ".join(reasons)
                    import_logger.warning(
                        " - %s (%s)",
                        format_transaction_summary(row),
                        reason_str,
                    )

    def _log_formatting_changes(
        self,
        original_df: pd.DataFrame,
    ) -> None:  # pragma: no cover
        """Log all retained formatting changes for debugging.

        Args:
            original_df: Original DataFrame before any formatting
        """
        if not import_logger.isEnabledFor(logging.DEBUG):
            return

        try:
            for column in self.formatted_df.columns:
                if column not in original_df.columns:
                    continue

                orig_str = original_df[column].astype(str).fillna("")
                curr_str = self.formatted_df[column].astype(str).fillna("")
                changed_mask = orig_str != curr_str

                if changed_mask.any():
                    for idx in changed_mask[changed_mask].index:
                        orig_val = (
                            original_df[column].iloc[idx]
                            if idx < len(original_df)
                            else ""
                        )
                        curr_val = (
                            self.formatted_df[column].iloc[idx]
                            if idx < len(self.formatted_df)
                            else ""
                        )
                        import_logger.debug(
                            AUTO_FORMAT_DEBUG,
                            idx,
                            column,
                            orig_val,
                            curr_val,
                        )
        except (TypeError, ValueError) as exc:
            import_logger.warning(
                "Comparison failed for column '%s' due to error: %s",
                column,
                exc,
            )

    def _format_dates(self) -> None:
        """Format date columns."""
        if Column.Txn.TXN_DATE in self.formatted_df.columns:
            self._format_date_column(Column.Txn.TXN_DATE, required=True)
        if Column.Txn.SETTLE_DATE in self.formatted_df.columns:
            self._format_date_column(Column.Txn.SETTLE_DATE, required=False)

        if self.config.optional_fields:
            for column in self.formatted_df.columns:
                optional_field = self.config.optional_fields.get_field(column)
                if optional_field and optional_field.field_type == FieldType.DATE:
                    self._format_date_column(column, required=False)

    def _format_date_column(self, column: str, *, required: bool) -> None:
        """Format all dates for the specified column."""
        col_series = self.formatted_df[column]
        if required:
            missing_mask = col_series.isna()
            if missing_mask.any():
                missing_indices = col_series[missing_mask].index
                self.exclusions.extend(missing_indices)
                for idx in missing_indices:
                    reason = f"MISSING {column}"
                    self.rejection_reasons.setdefault(idx, []).append(reason)
        else:
            self.formatted_df.loc[col_series.isna(), column] = pd.NA

        non_missing_mask = col_series.notna()
        if non_missing_mask.any():
            non_missing_series = col_series[non_missing_mask]
            parsed_dates = non_missing_series.apply(parse_date)
            invalid_mask = parsed_dates.isna()

            if invalid_mask.any():
                invalid_indices = non_missing_series[invalid_mask].index
                if required:
                    self.exclusions.extend(invalid_indices)
                    for idx in invalid_indices:
                        reason = f"INVALID {column}"
                        self.rejection_reasons.setdefault(idx, []).append(reason)
                else:
                    for idx in invalid_indices:
                        value = non_missing_series.loc[idx]
                        import_logger.debug(
                            "%d - Invalid optional date field '%s': '%s'",
                            idx,
                            column,
                            value,
                        )

        valid_parsed_mask = parsed_dates.notna()
        if valid_parsed_mask.any():
            valid_indices = non_missing_series[valid_parsed_mask].index
            self.formatted_df.loc[valid_indices, column] = parsed_dates[
                valid_parsed_mask
            ]

    def _format_actions(self) -> None:
        """Format action columns."""
        if Column.Txn.ACTION in self.formatted_df.columns:
            self._format_action_column(Column.Txn.ACTION, required=True)

        if self.config.optional_fields:
            for column in self.formatted_df.columns:
                optional_field = self.config.optional_fields.get_field(column)
                if optional_field and optional_field.field_type == FieldType.ACTION:
                    self._format_action_column(column, required=False)

    def _format_action_column(self, column: str, *, required: bool) -> None:
        """Format all actions for the specified column."""
        col_series = self.formatted_df[column]

        missing_mask = col_series.isna() | (col_series.astype(str).str.strip() == "")
        if missing_mask.any():
            missing_indices = col_series[missing_mask].index
            if required:
                self.exclusions.extend(missing_indices)
                for idx in missing_indices:
                    reason = f"MISSING {column}"
                    self.rejection_reasons.setdefault(idx, []).append(reason)
            else:
                self.formatted_df.loc[missing_indices, column] = pd.NA

        non_missing_mask = ~missing_mask
        if non_missing_mask.any():
            non_missing_series = col_series[~missing_mask]
            action_str_series = non_missing_series.astype(str).str.strip().str.upper()
            normalized_series = action_str_series.map(self.ACTION_MAP).fillna(
                action_str_series,
            )

            valid_actions_mask = normalized_series.isin(actions)
            invalid_mask = ~valid_actions_mask

            if invalid_mask.any():
                invalid_indices = non_missing_series[invalid_mask].index
                if required:
                    self.exclusions.extend(invalid_indices)
                    for idx in invalid_indices:
                        reason = f"INVALID {column}"
                        self.rejection_reasons.setdefault(idx, []).append(reason)
                else:
                    for idx in invalid_indices:
                        value = non_missing_series.loc[idx]
                        import_logger.debug(
                            "%d - Invalid optional action field '%s': '%s'",
                            idx,
                            column,
                            value,
                        )

        if valid_actions_mask.any():
            valid_indices = non_missing_series[valid_actions_mask].index
            self.formatted_df.loc[valid_indices, column] = normalized_series[
                valid_actions_mask
            ]

    def _format_currencies(self) -> None:
        """Format currency columns."""
        if Column.Txn.CURRENCY in self.formatted_df.columns:
            self._format_currency_column(Column.Txn.CURRENCY, required=True)

        if self.config.optional_fields:
            for column in self.formatted_df.columns:
                optional_field = self.config.optional_fields.get_field(column)
                if optional_field and optional_field.field_type == FieldType.CURRENCY:
                    self._format_currency_column(column, required=False)

    def _format_currency_column(self, column: str, *, required: bool) -> None:
        """Format all."""
        col_series = self.formatted_df[column]
        missing_mask = col_series.isna()
        if missing_mask.any():
            missing_indices = col_series[missing_mask].index
            if required:
                self.exclusions.extend(missing_indices)
                for idx in missing_indices:
                    reason = f"MISSING {column}"
                    self.rejection_reasons.setdefault(idx, []).append(reason)
            else:
                self.formatted_df.loc[missing_indices, column] = pd.NA

        non_missing_mask = ~missing_mask
        if non_missing_mask.any():
            non_missing_series = col_series[~missing_mask]
            currency_str_series = non_missing_series.astype(str).str.strip().str.upper()
            normalized_series = currency_str_series.map(self.CURRENCY_MAP).fillna(
                currency_str_series,
            )
            valid_currencies_mask = normalized_series.isin(currencies)
            invalid_mask = ~valid_currencies_mask

            if invalid_mask.any():
                invalid_indices = non_missing_series[invalid_mask].index
                if required:
                    self.exclusions.extend(invalid_indices)
                    for idx in invalid_indices:
                        reason = f"INVALID {column}"
                        self.rejection_reasons.setdefault(idx, []).append(reason)
                else:
                    for idx in invalid_indices:
                        value = non_missing_series.loc[idx]
                        import_logger.debug(
                            "%d - Invalid optional currency field '%s': '%s'",
                            idx,
                            column,
                            value,
                        )

        if valid_currencies_mask.any():
            valid_indices = non_missing_series[valid_currencies_mask].index
            self.formatted_df.loc[valid_indices, column] = normalized_series[
                valid_currencies_mask
            ]

    def _format_rule_columns(self) -> None:
        """Format rule based columns."""
        action_series = self.formatted_df[Column.Txn.ACTION]
        for action_value in action_series.dropna().unique():
            action_mask = action_series == action_value
            action_indices = self.formatted_df[action_mask].index

            if len(action_indices) == 0:  # pragma: no cover
                continue

            try:
                rules = ActionValidationRules.get_rules_for_action(str(action_value))
            except (KeyError, ValueError):
                rules = ActionValidationRules.DEFAULT

            import_logger.debug(
                "Processing %d rows with action %s using rules %s",
                len(action_indices),
                action_value,
                rules,
            )
            self._format_rows_with_rules(action_indices, rules)

        invalid_action_mask = action_series.isna()
        if invalid_action_mask.any():
            invalid_indices = self.formatted_df[invalid_action_mask].index
            self._format_rows_with_rules(invalid_indices, ActionValidationRules.DEFAULT)

    def _format_rows_with_rules(
        self,
        rows: pd.Index,
        rules: dict[str, list[str]],
    ) -> None:
        """Format all columns for given indices based on validation rules."""
        required_fields = set(rules["required_fields"])
        is_required = Column.Txn.TICKER in required_fields
        self._format_ticker_for_rows(
            Column.Txn.TICKER,
            rows,
            required=is_required,
        )

        numeric_fields: list[str] = [
            Column.Txn.AMOUNT,
            Column.Txn.PRICE,
            Column.Txn.UNITS,
            Column.Txn.FEE,
        ]

        if self.config.optional_fields:
            for column in self.formatted_df.columns:
                optional_field = self.config.optional_fields.get_field(column)
                if optional_field and optional_field.field_type == FieldType.NUMERIC:
                    numeric_fields.append(column)
                if optional_field and optional_field.field_type == FieldType.STRING:
                    self._format_string_for_rows(
                        column,
                        rows,
                        required=False,
                    )

        for field in numeric_fields:
            if field in self.formatted_df.columns:
                self.formatted_df[field] = self.formatted_df[field].astype("object")
                is_required = field in required_fields
                self._format_numeric_for_rows(
                    field,
                    rows,
                    required=is_required,
                )

    def _format_ticker_for_rows(
        self,
        column: str,
        indices: pd.Index,
        *,
        required: bool,
    ) -> None:
        """Vectorized formatting of ticker column for specific row indices."""
        col_series = self.formatted_df.loc[indices, column]
        missing_mask = col_series.isna() | (col_series.astype(str).str.strip() == "")
        if missing_mask.any():
            missing_indices = col_series[missing_mask].index
            if required:
                self.exclusions.extend(missing_indices)
                for idx in missing_indices:
                    reason = f"MISSING {column}"
                    self.rejection_reasons.setdefault(idx, []).append(reason)
            else:
                self.formatted_df.loc[missing_indices, column] = pd.NA

        non_missing_mask = ~missing_mask
        if non_missing_mask.any():
            non_missing_series = col_series[non_missing_mask]
            ticker_str_series = non_missing_series.astype(str).str.strip().str.upper()

            ticker_pattern = r"^[A-Z0-9.-]+$"
            valid_mask = ticker_str_series.str.match(ticker_pattern) & (
                ticker_str_series.str.len() > 0
            )
            invalid_mask = ~valid_mask

            if invalid_mask.any():
                invalid_indices = non_missing_series[invalid_mask].index
                if required:
                    self.exclusions.extend(invalid_indices)
                    for idx in invalid_indices:
                        reason = f"INVALID {column}"
                        self.rejection_reasons.setdefault(idx, []).append(reason)

            if valid_mask.any():
                valid_indices = non_missing_series[valid_mask].index
                self.formatted_df.loc[valid_indices, column] = ticker_str_series[
                    valid_mask
                ]

    def _format_string_for_rows(
        self,
        column: str,
        rows: pd.Index,
        *,
        required: bool,
    ) -> None:
        """Format string column for the given row indices."""
        col_series = self.formatted_df.loc[rows, column]
        missing_mask = col_series.isna() | (col_series.astype(str).str.strip() == "")
        if missing_mask.any():
            missing_indices = col_series[missing_mask].index
            if required:  # pragma: no cover
                self.exclusions.extend(missing_indices)
                for idx in missing_indices:
                    reason = f"MISSING {column}"
                    self.rejection_reasons.setdefault(idx, []).append(reason)
            else:
                self.formatted_df.loc[missing_indices, column] = pd.NA

        non_missing_mask = ~missing_mask
        if non_missing_mask.any():
            non_missing_series = col_series[non_missing_mask]
            trimmed_series = non_missing_series.astype(str).str.strip()
            self.formatted_df.loc[non_missing_series.index, column] = trimmed_series

    def _format_numeric_for_rows(
        self,
        column: str,
        indices: pd.Index,
        *,
        required: bool,
    ) -> None:
        """Format numeric column for the given row indices."""
        col_series = self.formatted_df.loc[indices, column]
        missing_mask = col_series.isna() | (col_series.astype(str).str.strip() == "")
        if missing_mask.any():
            missing_indices = col_series[missing_mask].index
            if required:
                self.exclusions.extend(missing_indices)
                for idx in missing_indices:
                    reason = f"MISSING {column}"
                    self.rejection_reasons.setdefault(idx, []).append(reason)
            else:
                self.formatted_df.loc[missing_indices, column] = pd.NA

        non_missing_mask = ~missing_mask
        if non_missing_mask.any():
            non_missing_series = col_series[non_missing_mask]
            cleaned_series = (
                non_missing_series.astype(str)
                .str.strip()
                .str.replace("$", "", regex=False)
                .str.replace(",", "", regex=False)
            )

            formatted_series = cleaned_series.apply(_to_decimal_format)
            invalid_mask = formatted_series.isna()
            if invalid_mask.any():
                invalid_indices = non_missing_series[invalid_mask].index
                if required:
                    self.exclusions.extend(invalid_indices)
                    for idx in invalid_indices:
                        reason = f"INVALID {column}"
                        self.rejection_reasons.setdefault(idx, []).append(reason)
                else:
                    for idx in invalid_indices:
                        value = non_missing_series.loc[idx]
                        import_logger.debug(
                            "%d - Invalid optional numeric field '%s': '%s'",
                            idx,
                            column,
                            value,
                        )

            valid_formatted_mask = formatted_series.notna()
            if valid_formatted_mask.any():
                valid_indices = non_missing_series[valid_formatted_mask].index
                self.formatted_df.loc[valid_indices, column] = formatted_series[
                    valid_formatted_mask
                ]

    def _calculate_settlement_dates(self) -> None:
        """Calculate settlement dates for transactions."""
        self.formatted_df = settlement_calculator.add_settlement_dates_to_dataframe(
            self.formatted_df,
        )


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
        except ValueError:
            continue

    return None
