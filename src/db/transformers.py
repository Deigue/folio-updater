"""Transaction transformation module for applying user-defined rules."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd

from app.app_context import get_config
from db.utils import format_transaction_summary
from utils.constants import Column
from utils.logging_setup import get_import_logger

if TYPE_CHECKING:
    from utils.transforms import MergeGroup, TransformRule

logger = logging.getLogger(__name__)
import_logger = get_import_logger()


class TransactionTransformer:
    """Transforms transaction data based on user-defined rules."""

    def __init__(self, df: pd.DataFrame) -> None:
        """Initialize the transformer with transaction data.

        Args:
            df: DataFrame with mapped transaction data
        """
        self.df: pd.DataFrame = df.copy()
        self.config = get_config()
        self.transforms = self.config.transforms
        self._has_groups: bool = False

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
        # Apply merge groups first (before regular transformations)
        if self.transforms and self.transforms.merge_groups:
            for group_index, group in enumerate(self.transforms.merge_groups):
                import_logger.debug(
                    "Applying merge group %d: %s",
                    group_index + 1,
                    group.name,
                )
                self._apply_merge_group(group)

        # Regular transformation rules
        if not self.transforms or not self.transforms.rules:
            import_logger.debug(
                "No transformation rules configured, skipping transforms",
            )
            return self.df

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

        # Apply transformations to matching rows
        for field_name, new_value in rule.actions.items():
            if field_name not in self.df.columns:
                import_logger.warning(
                    "Transform target field '%s' not found in data, skipping",
                    field_name,
                )
                continue

            # Log transformations
            old_values = self.df.loc[mask, field_name].unique().tolist()
            msg = (
                f"TRANSFORM '{field_name}' for {matching_rows} row(s): "
                f"{old_values} -> {new_value}"
            )
            import_logger.info(msg)

            if new_value == "":
                self.df.loc[mask, field_name] = pd.NA
            else:
                # Try to convert new_value to match the column's dtype to avoid warnings
                converted_value = self._convert_value_to_column_dtype(
                    field_name,
                    new_value,
                )
                self.df.loc[mask, field_name] = converted_value

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
            if not pd.isna(numeric_val):  # pragma: no cover
                numeric_values.append(numeric_val)

        # Numeric comparison for cases where DataFrame has numbers
        if numeric_values:  # pragma: no cover
            numeric_series = pd.to_numeric(series, errors="coerce")
            for numeric_value in numeric_values:
                numeric_mask = numeric_series == numeric_value
                masks.append(numeric_mask)

        # Combine all masks with OR logic
        combined_mask = masks[0]
        for mask in masks[1:]:  # pragma: no cover
            combined_mask = combined_mask | mask

        return combined_mask

    def _convert_value_to_column_dtype(
        self,
        field_name: str,
        new_value: str,
    ) -> str | float | int:
        """Convert new_value to match the column's dtype to avoid pandas warnings.

        Args:
            field_name: Name of the column being transformed
            new_value: String value to convert

        Returns:
            Value converted to appropriate type for the column
        """
        column_dtype = self.df[field_name].dtype

        # If column is numeric, try to convert the string to a number
        if pd.api.types.is_numeric_dtype(column_dtype):  # pragma: no cover
            try:
                # Try integer conversion first
                if column_dtype.kind in ["i", "u"]:
                    return int(new_value)
                # Otherwise try float conversion
                return float(new_value)
            except ValueError:
                # If conversion fails, log warning and return as string
                import_logger.warning(
                    "CONVERT FAIL '%s' to numeric for column '%s' (use string)",
                    new_value,
                    field_name,
                )
                return new_value

        # For non-numeric columns, return as string
        return new_value

    def _apply_merge_group(self, group: MergeGroup) -> None:
        """Apply a merge group to combine related transactions.

        Args:
            group: MergeGroup configuration specifying how to merge transactions
        """
        if not self._validate_merge_group_columns(group):  # pragma: no cover
            return

        matching_rows = self._get_matching_rows_for_merge(group)
        if matching_rows.empty:  # pragma: no cover
            return

        groups = matching_rows.groupby(group.match_fields, dropna=False)
        rows_to_drop = []
        rows_to_add = []

        for group_key, group_df in groups:
            merged_row = self._try_merge_group(group, group_df, group_key)
            if merged_row is not None:
                if not self._has_groups:
                    self._has_groups = True
                    import_logger.info(
                        "MERGE transactions",
                    )
                rows_to_drop.extend(group_df.index.tolist())
                rows_to_add.append(merged_row)

                import_logger.info(" + %s", format_transaction_summary(merged_row))

                if import_logger.isEnabledFor(logging.INFO):
                    dropped_rows = group_df.apply(
                        format_transaction_summary,
                        axis=1,
                    )
                    for row in dropped_rows:
                        import_logger.info(" - %s", row)

        # Apply changes to DataFrame
        self._apply_merge_changes(group, rows_to_drop, rows_to_add)

    def _validate_merge_group_columns(
        self,
        group: MergeGroup,
    ) -> bool:
        """Validate that all required columns exist for merge group.

        Args:
            group: MergeGroup configuration
            action_col: Name of the Action column

        Returns:
            True if all columns exist, False otherwise
        """
        missing_fields = [f for f in group.match_fields if f not in self.df.columns]
        if missing_fields:  # pragma: no cover
            import_logger.warning(
                "SKIP merge group '%s' (missing fields: %s)",
                group.name,
                missing_fields,
            )
            return False

        if group.amount_field not in self.df.columns:  # pragma: no cover
            import_logger.warning(
                "SKIP merge group '%s' (missing amount field: %s)",
                group.name,
                group.amount_field,
            )
            return False

        return True

    def _get_matching_rows_for_merge(
        self,
        group: MergeGroup,
    ) -> pd.DataFrame:
        """Get rows that match source actions for merging.

        Args:
            group: MergeGroup configuration
            action_col: Name of the Action column

        Returns:
            DataFrame with matching rows
        """
        action_mask = self.df[Column.Txn.ACTION].astype(str).isin(group.source_actions)
        matching_rows = self.df[action_mask].copy()

        if matching_rows.empty:  # pragma: no cover
            import_logger.debug(
                "No rows match source actions %s for merge group '%s'",
                group.source_actions,
                group.name,
            )
        else:
            import_logger.debug(
                "Found %d row(s) matching source actions for merge group '%s'",
                len(matching_rows),
                group.name,
            )

        return matching_rows

    def _try_merge_group(
        self,
        group: MergeGroup,
        group_df: pd.DataFrame,
        group_key: tuple,
    ) -> pd.Series | None:
        """Try to merge a group of transactions.

        Args:
            group: MergeGroup configuration
            group_df: DataFrame with transactions in this group
            action_col: Name of the Action column
            group_key: Tuple identifying this group

        Returns:
            Merged row as Series, or None if merging not applicable
        """
        # Check if we have multiple source actions in this group
        unique_actions = group_df[Column.Txn.ACTION].astype(str).unique()
        min_actions_for_merge = 2
        if len(unique_actions) < min_actions_for_merge:
            return None

        # Check if we have all expected source actions
        has_all_actions = all(
            action in unique_actions for action in group.source_actions
        )
        if not has_all_actions:  # pragma: no cover
            import_logger.debug(
                "Group %s missing some source actions, skipping",
                group_key,
            )
            return None

        merged_row = self._create_merged_row(group, group_df)

        import_logger.debug(
            "Merged %d row(s) into 1 %s for group %s",
            len(group_df),
            group.target_action,
            group_key,
        )

        return merged_row

    def _create_merged_row(
        self,
        group: MergeGroup,
        group_df: pd.DataFrame,
    ) -> pd.Series:
        """Create a merged row from a group of transactions.

        Args:
            group: MergeGroup configuration
            group_df: DataFrame with transactions to merge
            action_col: Name of the Action column

        Returns:
            Merged row as Series
        """
        # Sum the amounts - ensure numeric conversion and cast to Decimal
        amount_series = pd.to_numeric(group_df[group.amount_field], errors="coerce")
        total_amount = Decimal(str(amount_series.sum())).quantize(
            Decimal("0.0000000001"),  # 10 decimal places (decimal[20,10])
        )

        # Create merged row (use first row as template)
        merged_row = group_df.iloc[0].copy()
        merged_row[Column.Txn.ACTION] = group.target_action
        merged_row[group.amount_field] = float(total_amount)

        for field, value in group.operations.items():
            if field in merged_row.index:
                converted_value = self._convert_value_to_column_dtype(
                    field,
                    str(value),
                )
                merged_row[field] = converted_value

        return merged_row

    def _apply_merge_changes(
        self,
        group: MergeGroup,
        rows_to_drop: list[int],
        rows_to_add: list[pd.Series],
    ) -> None:
        """Apply merge changes to the DataFrame.

        Args:
            group: MergeGroup configuration
            rows_to_drop: List of row indices to remove
            rows_to_add: List of merged rows to add
        """
        if rows_to_drop:
            self.df = self.df.drop(index=rows_to_drop)
            if rows_to_add:
                new_rows_df = pd.DataFrame(rows_to_add)
                self.df = pd.concat([self.df, new_rows_df], ignore_index=True)

            import_logger.debug(
                "Merge group '%s': Removed %d row(s), added %d merged row(s)",
                group.name,
                len(rows_to_drop),
                len(rows_to_add),
            )
