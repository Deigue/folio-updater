"""Validating and coercing transaction fields.

The second pipeline stage: numbers into a common decimal format, tickers and
strings normalised, signs forced to match the action, and settlement dates
filled where the source left them out. A row that cannot be made valid is
rejected here with a reason.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, ClassVar

import pandas as pd
from pandas import Series

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

from app import get_config
from db.helpers import format_transaction_summary
from utils import TORONTO_TZ, Action, Column, Currency, Sign, get_import_logger
from utils.optional_fields import FieldType
from utils.settlement_calculator import settlement_calculator

logger = logging.getLogger(__name__)
import_logger = get_import_logger()
actions: list[str] = [action.value for action in Action]
currencies: set[str] = {currency.value for currency in Currency}
AUTO_FORMAT_DEBUG: str = "%d - Auto-formatted %s: '%s' -> '%s'"
NON_NUMERIC: str = "NON-NUMERIC"


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
        # A transfer carries either cash or units, so neither is required on its
        # own: a cash transfer has Amount and no Ticker, a position transfer has
        # Ticker and Units and no Amount.
        Action.TFR_IN: {
            "required_fields": [Column.Txn.ACCOUNT, Column.Txn.CURRENCY],
            "optional_fields": [
                Column.Txn.AMOUNT,
                Column.Txn.PRICE,
                Column.Txn.UNITS,
                Column.Txn.TICKER,
                Column.Txn.FEE,
            ],
        },
        Action.TFR_OUT: {
            "required_fields": [Column.Txn.ACCOUNT, Column.Txn.CURRENCY],
            "optional_fields": [
                Column.Txn.AMOUNT,
                Column.Txn.PRICE,
                Column.Txn.UNITS,
                Column.Txn.TICKER,
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

    # Required signs for each action
    SIGN_RULES: ClassVar[dict[Action, dict[str, Sign]]] = {
        Action.BUY: {
            Column.Txn.AMOUNT: Sign.NEGATIVE,  # cash out
            Column.Txn.UNITS: Sign.POSITIVE,  # shares acquired
        },
        Action.SELL: {
            Column.Txn.AMOUNT: Sign.POSITIVE,  # cash in
            Column.Txn.UNITS: Sign.NEGATIVE,  # shares disposed
        },
        Action.WITHDRAWAL: {Column.Txn.AMOUNT: Sign.NEGATIVE},
        Action.CONTRIBUTION: {Column.Txn.AMOUNT: Sign.POSITIVE},
        Action.ROC: {Column.Txn.AMOUNT: Sign.POSITIVE},
        Action.SPLIT: {
            Column.Txn.PRICE: Sign.POSITIVE,
            Column.Txn.UNITS: Sign.POSITIVE,
        },
        Action.TFR_IN: {
            Column.Txn.AMOUNT: Sign.POSITIVE,  # cash in
            Column.Txn.UNITS: Sign.POSITIVE,  # shares in
        },
        Action.TFR_OUT: {
            Column.Txn.AMOUNT: Sign.NEGATIVE,  # cash out
            Column.Txn.UNITS: Sign.NEGATIVE,  # shares out
        },
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

    @classmethod
    def get_sign_rules_for_action(cls, action: str) -> dict[str, Sign]:
        """Get the required Amount/Units signs for a specific action.

        Args:
            action: The action type as a string

        Returns:
            Mapping of column name to required sign. Empty when the action
            places no constraint on either field.
        """
        try:
            action_enum = Action(action)
        except ValueError:
            return {}
        return cls.SIGN_RULES.get(action_enum, {})


def _to_decimal_format(value: str) -> str | None:
    """Try convert verified string to a plain decimal string."""
    try:
        decimal_value = Decimal(str(value))
        return format(decimal_value, "f")
    except (ValueError, TypeError, InvalidOperation):
        return None


_TICKER_PATTERN = re.compile(r"^[A-Z0-9.-]+$")


def _normalize_numeric(value: object) -> str | None:
    """Strip currency dressing off a numeric cell, or None if it isn't one."""
    cleaned = str(value).strip().replace("$", "").replace(",", "")
    return _to_decimal_format(cleaned)


def _normalize_ticker(value: object) -> str | None:
    """Upper-case a ticker, or None if it holds characters a symbol cannot."""
    ticker = str(value).strip().upper()
    return ticker if _TICKER_PATTERN.match(ticker) else None


def _normalize_string(value: object) -> str:
    """Trim a free-text cell. Any text is acceptable, so this never rejects."""
    return str(value).strip()


def _negate_decimal(value: str) -> str:
    """Flip the sign of a numeric value, preserving its decimal precision."""
    try:
        return format(-Decimal(value), "f")
    except (ValueError, TypeError, InvalidOperation):  # pragma: no cover
        return value


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
        "TRANSFER_IN": "TFR_IN",
        "TRANSFER IN": "TFR_IN",
        "TFRIN": "TFR_IN",
        "TRANSFER_OUT": "TFR_OUT",
        "TRANSFER OUT": "TFR_OUT",
        "TFROUT": "TFR_OUT",
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
        self._normalize_signs()
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
                    **{str(Column.REJECTION_REASON): reasons_list},
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

    def _apply_column_rules(  # noqa: PLR0913
        self,
        column: str,
        indices: pd.Index,
        normalize: Callable[[object], str | None],
        *,
        required: bool,
        blank_is_missing: bool = True,
        reject_invalid: bool = False,
        invalid_debug: str | None = None,
    ) -> None:
        """Normalise one column's rows in a single pass, recording rejects.

        Args:
            column: Column being formatted.
            indices: Rows to touch. Rule-based callers pass one action's rows.
            normalize: Maps raw value -> stored form, or None to reject.
            required: Whether a missing or invalid value disqualifies the row.
            blank_is_missing: Whether whitespace-only counts as missing.
            reject_invalid: Whether an unusable value disqualifies the row.
            invalid_debug: Log template for optional values that fail to normalise.
        """
        col_series = self.formatted_df.loc[indices, column]
        labels: list[int] = []
        values: list[Any] = []
        missing: list[int] = []
        invalid: list[tuple[int, object]] = []

        for label, raw, is_na in zip(
            col_series.index.to_numpy(),
            col_series.to_numpy(),
            col_series.isna().to_numpy(),
            strict=True,
        ):
            if is_na or (blank_is_missing and not str(raw).strip()):
                missing.append(label)
                if not required:
                    labels.append(label)
                    values.append(pd.NA)
                continue

            normalized = normalize(raw)
            if normalized is None:
                invalid.append((label, raw))
                continue

            labels.append(label)
            values.append(normalized)

        self._record_rejects(
            column,
            missing,
            invalid,
            required=required,
            reject_invalid=reject_invalid,
            invalid_debug=invalid_debug,
        )

        if labels:
            self.formatted_df.loc[labels, column] = values

    def _record_rejects(  # noqa: PLR0913
        self,
        column: str,
        missing: Sequence[int],
        invalid: Sequence[tuple[int, object]],
        *,
        required: bool,
        reject_invalid: bool,
        invalid_debug: str | None,
    ) -> None:
        """Book missing and invalid rows against the audit trail."""
        if required:
            self.exclusions.extend(missing)
            for label in missing:
                self.rejection_reasons.setdefault(label, []).append(
                    f"MISSING {column}",
                )

        if not (required or reject_invalid):
            if invalid_debug:
                for label, raw in invalid:
                    import_logger.debug(invalid_debug, label, column, raw)
            return

        reason = f"INVALID {column}" if required else f"{NON_NUMERIC} {column}"
        self.exclusions.extend(label for label, _ in invalid)
        for label, _ in invalid:
            self.rejection_reasons.setdefault(label, []).append(reason)

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
        """Format the date column."""
        self._apply_column_rules(
            column,
            self.formatted_df.index,
            parse_date,
            required=required,
            # An empty date cell is a value that failed to parse, not an
            # absent one, so it is reported as INVALID rather than MISSING.
            blank_is_missing=False,
            invalid_debug="%d - Invalid optional date field '%s': '%s'",
        )

    def _format_actions(self) -> None:
        """Format action columns."""
        if Column.Txn.ACTION in self.formatted_df.columns:
            self._format_action_column(Column.Txn.ACTION, required=True)

        if self.config.optional_fields:
            for column in self.formatted_df.columns:
                optional_field = self.config.optional_fields.get_field(column)
                if optional_field and optional_field.field_type == FieldType.ACTION:
                    self._format_action_column(column, required=False)

    def _normalize_action(self, value: object) -> str | None:
        """Resolve an action alias (`bought`, `DIV`) to its canonical name."""
        action = str(value).strip().upper()
        mapped = self.ACTION_MAP.get(action, action)
        return mapped if mapped in actions else None

    def _format_action_column(self, column: str, *, required: bool) -> None:
        """Format the action column."""
        self._apply_column_rules(
            column,
            self.formatted_df.index,
            self._normalize_action,
            required=required,
            invalid_debug="%d - Invalid optional action field '%s': '%s'",
        )

    def _format_currencies(self) -> None:
        """Format currency columns."""
        if Column.Txn.CURRENCY in self.formatted_df.columns:
            self._format_currency_column(Column.Txn.CURRENCY, required=True)

        if self.config.optional_fields:
            for column in self.formatted_df.columns:
                optional_field = self.config.optional_fields.get_field(column)
                if optional_field and optional_field.field_type == FieldType.CURRENCY:
                    self._format_currency_column(column, required=False)

    def _normalize_currency(self, value: object) -> str | None:
        """Resolve a currency alias (`US$`, `canadian`) to its ISO code."""
        currency = str(value).strip().upper()
        mapped = self.CURRENCY_MAP.get(currency, currency)
        return mapped if mapped in currencies else None

    def _format_currency_column(self, column: str, *, required: bool) -> None:
        """Format the currency column."""
        self._apply_column_rules(
            column,
            self.formatted_df.index,
            self._normalize_currency,
            required=required,
            # As with dates, a blank currency is reported as INVALID.
            blank_is_missing=False,
            invalid_debug="%d - Invalid optional currency field '%s': '%s'",
        )

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

    def _normalize_signs(self) -> None:
        """Correct Amount/Units signs to match each action's cash-flow direction."""
        action_series: Series = self.formatted_df[Column.Txn.ACTION]
        for action_value in action_series.dropna().unique():
            sign_rules = ActionValidationRules.get_sign_rules_for_action(
                str(action_value),
            )
            if not sign_rules:
                continue

            action_indices = self.formatted_df[action_series == action_value].index
            for column, sign in sign_rules.items():
                if column in self.formatted_df.columns:
                    self._apply_sign_for_rows(
                        column,
                        action_indices,
                        sign,
                    )

    def _apply_sign_for_rows(
        self,
        column: str,
        indices: pd.Index,
        sign: Sign,
    ) -> None:
        """Flip values in a column that contradict the required sign.

        Zero and non-numeric values are left alone: zero has no direction, and
        anything unparseable has already been handled by numeric formatting.
        """
        col_series = self.formatted_df.loc[indices, column]
        numeric = pd.to_numeric(col_series, errors="coerce")
        wrong_mask = numeric > 0 if sign is Sign.NEGATIVE else numeric < 0

        if not wrong_mask.any():
            return

        # Detection is vectorized on floats, but the correction goes back
        # through Decimal so NUMERIC(20,10) precision survives the flip.
        wrong_series = col_series[wrong_mask]
        corrected = wrong_series.apply(_negate_decimal)
        self.formatted_df.loc[wrong_series.index, column] = corrected

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
        """Format the ticker column for specific row indices."""
        # No invalid_debug: an unusable optional ticker is left as it was found,
        # without a log line, which is what this column has always done.
        self._apply_column_rules(
            column,
            indices,
            _normalize_ticker,
            required=required,
        )

    def _format_string_for_rows(
        self,
        column: str,
        rows: pd.Index,
        *,
        required: bool,
    ) -> None:
        """Format string column for the given row indices."""
        self._apply_column_rules(
            column,
            rows,
            _normalize_string,
            required=required,
        )

    def _format_numeric_for_rows(
        self,
        column: str,
        indices: pd.Index,
        *,
        required: bool,
    ) -> None:
        """Format numeric column for the given row indices."""
        self._apply_column_rules(
            column,
            indices,
            _normalize_numeric,
            required=required,
            reject_invalid=True,
        )

    def _calculate_settlement_dates(self) -> None:
        """Calculate settlement dates for transactions."""
        self.formatted_df = settlement_calculator.add_settlement_dates_to_dataframe(
            self.formatted_df,
        )


def parse_date(date_str: object) -> str | None:
    """Parse various date formats to YYYY-MM-DD.

    Args:
        date_str: Date value in various formats. Anything str() can render is
            accepted; raw cells arrive here straight from an imported frame.

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
