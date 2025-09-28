"""Transaction transformation module for applying user-defined rules."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pandas as pd

from app.app_context import get_config
from utils.logging_setup import get_import_logger

if TYPE_CHECKING:
    from utils.transforms import TransformRule

logger = logging.getLogger(__name__)
import_logger = get_import_logger()


class TransactionTransformer:
    """Transforms transaction data based on user-defined rules."""

    def __init__(self, df: pd.DataFrame) -> None:
        """Initialize the transformer with transaction data.

        Args:
            df: DataFrame with mapped transaction data
        """
        self.df = df.copy()
        self.config = get_config()
        self.transforms = self.config.transforms

    @staticmethod
    def transform(df: pd.DataFrame) -> pd.DataFrame:
        """Transform transaction data based on configured rules.

        Args:
            df: DataFrame with mapped transaction data

        Returns:
            DataFrame with transformations applied
        """
        if df.empty:  # pragma: no cover
            return df

        transformer = TransactionTransformer(df)
        return transformer._apply_transforms()

    def _apply_transforms(self) -> pd.DataFrame:
        """Apply all transformation rules to the DataFrame.

        Returns:
            DataFrame with all applicable transformations applied
        """
        if not self.transforms or not self.transforms.rules:
            import_logger.debug(
                "No transformation rules configured, skipping transforms",
            )
            return self.df

        import_logger.info(
            "Applying %d transformation rule(s)",
            len(self.transforms.rules),
        )

        for rule_index, rule in enumerate(self.transforms.rules):
            import_logger.debug("Applying transformation rule %d", rule_index + 1)
            self._apply_single_rule(rule)

        return self.df

    def _apply_single_rule(self, rule: TransformRule) -> None:
        """Apply a single transformation rule to matching rows.

        Args:
            rule: The transformation rule to apply
        """
        # Create a boolean mask for rows that match all conditions
        mask = pd.Series([True] * len(self.df), index=self.df.index)

        for field_name, match_values in rule.conditions.items():
            if field_name not in self.df.columns:
                import_logger.debug(
                    "Condition field '%s' not found in data, skipping rule",
                    field_name,
                )
                return

            # Create condition mask: field value must match one of the specified values
            field_mask = self._create_field_mask(field_name, match_values)
            mask = mask & field_mask

        # Count matching rows
        matching_rows = mask.sum()
        if matching_rows == 0:
            import_logger.debug("No rows match transformation conditions")
            return

        import_logger.info(
            "Transforming %d row(s) matching conditions: %s",
            matching_rows,
            dict(rule.conditions),
        )

        # Apply transformations to matching rows
        for field_name, new_value in rule.actions.items():
            if field_name not in self.df.columns:
                import_logger.warning(
                    "Transform target field '%s' not found in data, skipping",
                    field_name,
                )
                continue

            # Log before transformation for debugging
            old_values = self.df.loc[mask, field_name].unique().tolist()
            import_logger.debug(
                "Transforming field '%s': %s -> %s",
                field_name,
                old_values,
                new_value,
            )

            if new_value == "":
                self.df.loc[mask, field_name] = pd.NA
            else:
                self.df.loc[mask, field_name] = new_value

    def _create_field_mask(
        self,
        field_name: str,
        match_values: list[str],
    ) -> pd.Series:
        """Create a boolean mask for field matching.

        Args:
            field_name: Name of the field to match against
            match_values: List of string values to match (converted from original types)

        Returns:
            Boolean Series indicating which rows match the condition
        """
        series = self.df[field_name]
        masks = []

        # Direct comparison (handles string matches and exact numeric matches)
        string_mask = series.astype(str).isin(match_values)
        masks.append(string_mask)

        numeric_values = []
        for match_value in match_values:
            numeric_val = pd.to_numeric(match_value, errors="coerce")
            if not pd.isna(numeric_val):
                numeric_values.append(numeric_val)

        # Numeric comparison for cases where DataFrame has numbers
        if numeric_values:
            numeric_series = pd.to_numeric(series, errors="coerce")
            for numeric_value in numeric_values:
                numeric_mask = numeric_series == numeric_value
                masks.append(numeric_mask)

        # Combine all masks with OR logic
        combined_mask = masks[0]
        for mask in masks[1:]:
            combined_mask = combined_mask | mask

        return combined_mask
