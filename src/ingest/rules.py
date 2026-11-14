"""Applying the user's import rules: cancellation, merge, then transform."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from app import audit, get_config
from app.audit import AuditMark, format_transaction_summary
from domain import NOTHING_TO_VOID, Column
from domain.numeric import HALF_CENT
from ingest.validation import normalize_numeric
from models import CancelEvent, MergeEvent, TransformEvent

if TYPE_CHECKING:
    from collections.abc import Mapping

    from models.import_rules import CancelRule, MergeGroup, TransformRule


CONTAINS_PREFIX = "contains:"
# A cancellation and the row it voids carry amounts that sum to zero.
_CANCEL_TOLERANCE = float(HALF_CENT)


@dataclass(frozen=True)
class RuleOutcome:
    """What the import rules did to a frame.

    Attributes:
        df: The rows left after every rule has run.
        merges: One event per merged transaction.
        transforms: One event per field a transform rule changed.
        cancels: One event per cancellation that voided a row.
        invalid: Cancelling rows with nothing in the file to void, rejected as
            invalid rows, each with its reason.
    """

    df: pd.DataFrame
    merges: list[MergeEvent]
    transforms: list[TransformEvent]
    cancels: list[CancelEvent]
    invalid: pd.DataFrame


class TransactionTransformer:
    """Transforms transaction data based on user-defined rules."""

    def __init__(self, df: pd.DataFrame) -> None:
        """Initialize the transformer with transaction data.

        Args:
            df: DataFrame with mapped transaction data
        """
        self.df: pd.DataFrame = df.copy()
        self._config = get_config()
        self.transforms = self._config.transforms
        self._cancel_rules_seen: set[str] = set()
        self._merge_events: list[MergeEvent] = []
        self._transform_events: list[TransformEvent] = []
        self._cancel_events: list[CancelEvent] = []
        self._invalid: list[pd.Series] = []

    @staticmethod
    def transform(df: pd.DataFrame) -> RuleOutcome:
        """Apply the import rules, recording a cancel, merge or transform event each.

        Args:
            df: DataFrame with mapped transaction data

        Returns:
            The transformed rows and the events the rules produced.
        """
        if df.empty:  # pragma: no cover
            return RuleOutcome(df, [], [], [], pd.DataFrame())
        transformer = TransactionTransformer(df)
        transformer._apply_transforms()
        return RuleOutcome(
            transformer.df,
            transformer._merge_events,
            transformer._transform_events,
            transformer._cancel_events,
            transformer._invalid_cancellations(),
        )

    @staticmethod
    def transform_fields(fields: Mapping[str, object]) -> dict[str, object]:
        """Apply the user's transform rules to a single already-mapped row.

        For callers outside the import pipeline that hold one row rather than a
        frame: a statement row being matched against the folio has to wear the
        same ticker the rules gave the transaction when it was imported. Merge
        groups are skipped, since combining rows is meaningless for one row.

        A rule whose conditions name a field absent from `fields` is skipped, so
        pass every field the rules might key on.

        Args:
            fields: Column name to value, keyed by internal (mapped) names.

        Returns:
            The same fields with every applicable rule applied.
        """
        transformer = TransactionTransformer(pd.DataFrame([dict(fields)]))
        if not transformer.transforms or not transformer.transforms.rules:
            return dict(fields)
        for rule in transformer.transforms.rules:
            transformer._apply_single_rule(rule)
        row = transformer.df.iloc[0]
        return {str(name): row[name] for name in transformer.df.columns}

    def _apply_transforms(self) -> None:
        """Apply the cancellation, merge and transform rules to the DataFrame."""
        # Cancellations first, while actions still read as the broker wrote them
        if self.transforms and self.transforms.cancellations:
            for rule in self.transforms.cancellations:
                self._apply_cancel_rule(rule)

        # Apply merge groups next (before regular transformations)
        if self.transforms and self.transforms.merge_groups:
            for group_index, group in enumerate(self.transforms.merge_groups):
                audit.debug(
                    "Applying merge group %d: %s",
                    group_index + 1,
                    group.name,
                )
                self._apply_merge_group(group)

        # Regular transformation rules
        if not self.transforms or not self.transforms.rules:
            audit.debug(
                "No transformation rules configured, skipping transforms",
            )
            return

        for rule_index, rule in enumerate(self.transforms.rules):
            audit.debug("Applying transformation rule %d", rule_index + 1)
            self._apply_single_rule(rule)

    def _apply_single_rule(self, rule: TransformRule) -> None:
        """Apply a single transformation rule to matching rows.

        Args:
            rule: The transformation rule to apply
        """
        mask = self._conditions_mask(rule.conditions)
        if mask is None:
            return

        # Count matching rows
        matching_rows = mask.sum()
        if matching_rows == 0:
            audit.debug("No rows match transformation conditions")
            return

        # Apply transformations to matching rows
        for field_name, new_value in rule.actions.items():
            if field_name not in self.df.columns:
                audit.stage(
                    "SKIP transform of '%s': the file has no such field",
                    field_name,
                    level=logging.WARNING,
                )
                continue

            # Log transformations
            old_values = list(self.df.loc[mask, field_name].unique())
            audit.stage(
                "TRANSFORM %s %s -> %s (%d row(s))",
                field_name,
                ", ".join(map(str, old_values)),
                new_value,
                matching_rows,
            )

            if new_value == "":
                self.df.loc[mask, field_name] = pd.NA
            else:
                converted_value = self._convert_value_to_column_dtype(
                    field_name,
                    new_value,
                )
                column = self.df[field_name]
                if isinstance(converted_value, str) and pd.api.types.is_numeric_dtype(
                    column,
                ):
                    # pandas refuses text in a number column rather than widening it.
                    self.df[field_name] = column.astype(object)
                self.df.loc[mask, field_name] = converted_value

            self._transform_events.append(
                TransformEvent(
                    field_name=field_name,
                    old_values=old_values,
                    new_value=new_value,
                    row_count=int(matching_rows),
                ),
            )

    def _conditions_mask(self, conditions: dict[str, list[str]]) -> pd.Series | None:
        """Mark the rows that meet every condition.

        Args:
            conditions: Field name to the values that field may hold.

        Returns:
            A boolean mask over the rows, or None when a condition names a field
            the data does not have (the rule cannot apply).
        """
        mask = pd.Series([True] * len(self.df), index=self.df.index)
        for field_name, match_values in conditions.items():
            if field_name not in self.df.columns:
                audit.debug(
                    "Condition field '%s' not found in data, skipping rule",
                    field_name,
                )
                return None
            # Field value must match one of the specified values
            mask = mask & self._create_field_mask(field_name, match_values)
        return mask

    def _apply_cancel_rule(self, rule: CancelRule) -> None:
        """Drop each cancelling row together with the row it voids.

        A cancelling row voids the earliest row on or before it that shares
        every match field and carries the opposite amount. A stored row is never
        matched: its sign and action have been normalized since, so a
        cancellation cannot be told from a genuine row. One with nothing to void
        in this file is rejected instead, so it is never silently lost.

        Args:
            rule: The cancellation rule to apply.
        """
        needed = [*rule.match_fields, rule.amount_field]
        missing = [name for name in needed if name not in self.df.columns]
        if missing:
            audit.stage(
                "SKIP cancellation rule '%s' (missing fields: %s)",
                rule.name,
                ", ".join(map(str, missing)),
                level=logging.WARNING,
            )
            return
        mask = self._conditions_mask(rule.conditions)
        if mask is None or not mask.any():
            return

        amounts = pd.to_numeric(
            self.df[rule.amount_field].map(normalize_numeric),
            errors="coerce",
        )
        dates = pd.to_datetime(
            self.df.get(Column.Txn.TXN_DATE, pd.Series(index=self.df.index)),
            errors="coerce",
        )
        keys = self.df[rule.match_fields].astype(str)
        positions = np.arange(len(self.df))
        dropped: list[int] = []
        for pos in np.flatnonzero(mask.to_numpy()):
            cancel_date = dates.iloc[pos]
            candidates = (
                ~mask.to_numpy()
                & ~np.isin(positions, dropped)
                & (keys == keys.iloc[pos]).all(axis=1).to_numpy()
                & ((amounts + amounts.iloc[pos]).abs() < _CANCEL_TOLERANCE).to_numpy()
                & (
                    dates.isna() | pd.isna(cancel_date) | (dates <= cancel_date)
                ).to_numpy()
            )
            found = np.flatnonzero(candidates)
            # Earliest first; rows with no readable date go last.
            order = np.argsort(
                dates.iloc[found].to_numpy(na_value=np.datetime64("NaT", "ns")),
                kind="stable",
            )
            dropped.append(int(pos))
            if not len(found):
                self._reject_cancellation(rule, self.df.iloc[pos])
                continue
            voided = int(found[order[0]])
            self._record_cancel(rule, self.df.iloc[pos], self.df.iloc[voided])
            dropped.append(voided)

        self.df = self.df.drop(index=self.df.index[dropped])

    def _record_cancel(
        self,
        rule: CancelRule,
        cancellation: pd.Series,
        cancelled: pd.Series,
    ) -> None:
        """Log one cancellation and keep it for the import audit."""
        self._cancel_stage(rule)
        audit.row(AuditMark.VOIDED, format_transaction_summary(cancellation))
        audit.row(AuditMark.VOIDED, format_transaction_summary(cancelled), depth=3)
        self._cancel_events.append(
            CancelEvent(
                rule=rule,
                cancellation={str(k): v for k, v in cancellation.items()},
                cancelled={str(k): v for k, v in cancelled.items()},
            ),
        )

    def _reject_cancellation(self, rule: CancelRule, cancellation: pd.Series) -> None:
        """Log a cancelling row with nothing to void, and keep it to be rejected."""
        self._cancel_stage(rule)
        audit.row(
            AuditMark.REMOVED,
            f"{format_transaction_summary(cancellation)} ({NOTHING_TO_VOID})",
            level=logging.WARNING,
        )
        self._invalid.append(cancellation)

    def _cancel_stage(self, rule: CancelRule) -> None:
        """Open the audit stage of a rule, once per import."""
        if rule.name not in self._cancel_rules_seen:
            self._cancel_rules_seen.add(rule.name)
            audit.stage("CANCEL '%s'", rule.name)

    def _invalid_cancellations(self) -> pd.DataFrame:
        """Return the cancelling rows with nothing to void, each with its reason."""
        if not self._invalid:
            return pd.DataFrame()
        return pd.DataFrame(self._invalid).assign(
            **{str(Column.REJECTION_REASON): NOTHING_TO_VOID},
        )

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

        # contains:PHRASE" matches on substring instead of equality.
        literals: list[str] = []
        for match_value in match_values:
            text = str(match_value)
            if text.startswith(CONTAINS_PREFIX):
                phrase = text[len(CONTAINS_PREFIX) :]
                masks.append(
                    series.astype(str).str.contains(phrase, case=False, regex=False),
                )
            else:
                literals.append(match_value)

        # Direct comparison (handles string matches and exact numeric matches)
        string_mask = series.astype(str).isin(literals)
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
        if pd.api.types.is_numeric_dtype(column_dtype):
            try:
                # Try integer conversion first
                if column_dtype.kind in ["i", "u"]:
                    return int(new_value)
                # Otherwise try float conversion
                return float(new_value)
            except ValueError:
                # If conversion fails, log warning and return as string
                audit.stage(
                    "KEEP '%s' as text in '%s': it is not a number",
                    new_value,
                    field_name,
                    level=logging.WARNING,
                )
                return new_value

        # For non-numeric columns, return as string
        return new_value

    def _apply_merge_group(self, group: MergeGroup) -> None:
        """Apply a merge group to combine related transactions.

        Args:
            group: MergeGroup configuration specifying how to merge transactions
        """
        if not self._validate_merge_group_columns(group):
            return

        matching_rows = self._get_matching_rows_for_merge(group)
        if matching_rows.empty:
            return

        groups = matching_rows.groupby(group.match_fields, dropna=False)
        rows_to_drop = []
        rows_to_add = []

        for group_key, group_df in groups:
            merged_row = self._try_merge_group(group, group_df, group_key)
            if merged_row is not None:
                if not rows_to_add:
                    audit.stage("MERGE '%s'", group.name)
                rows_to_drop.extend(group_df.index.tolist())
                rows_to_add.append(merged_row)

                if audit.enabled():
                    audit.row(AuditMark.STORED, format_transaction_summary(merged_row))
                    for row in group_df.apply(format_transaction_summary, axis=1):
                        audit.row(AuditMark.REMOVED, row, depth=3)

                self._merge_events.append(
                    MergeEvent(
                        merged_row={str(k): v for k, v in merged_row.items()},
                        source_rows=group_df.copy(),
                    ),
                )

        self._apply_merge_changes(group, rows_to_drop, rows_to_add)

    def _validate_merge_group_columns(
        self,
        group: MergeGroup,
    ) -> bool:
        """Validate that all required columns exist for merge group.

        Args:
            group: MergeGroup configuration

        Returns:
            True if all columns exist, False otherwise
        """
        missing_fields = [f for f in group.match_fields if f not in self.df.columns]
        if missing_fields:
            audit.stage(
                "SKIP merge group '%s' (missing fields: %s)",
                group.name,
                ", ".join(map(str, missing_fields)),
                level=logging.WARNING,
            )
            return False

        if group.amount_field not in self.df.columns:
            audit.stage(
                "SKIP merge group '%s' (missing amount field: %s)",
                group.name,
                group.amount_field,
                level=logging.WARNING,
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

        Returns:
            DataFrame with matching rows
        """
        action_mask = self.df[Column.Txn.ACTION].astype(str).isin(group.source_actions)
        matching_rows = self.df[action_mask].copy()

        if matching_rows.empty:
            audit.debug(
                "No rows match source actions %s for merge group '%s'",
                group.source_actions,
                group.name,
            )
        else:
            audit.debug(
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
        if not has_all_actions:
            audit.debug(
                "Group %s missing some source actions, skipping",
                group_key,
            )
            return None

        merged_row = self._create_merged_row(group, group_df)

        audit.debug(
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

        Returns:
            Merged row as Series
        """
        # Sum the amounts - ensure numeric conversion and cast to Decimal
        amount_series = pd.to_numeric(group_df[group.amount_field], errors="coerce")
        total_amount = Decimal(str(amount_series.sum())).quantize(
            Decimal("0.0000000001"),  # 10 decimal places (decimal[20,10])
        )

        # The first row is the template. A row of an all-text file is itself text,
        # and pandas refuses a number in it, so it is widened to hold any value.
        merged_row = group_df.iloc[0].astype(object)
        merged_row[Column.Txn.ACTION] = group.target_action
        merged_row[group.amount_field] = self._convert_value_to_column_dtype(
            group.amount_field,
            str(total_amount),
        )

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

            audit.debug(
                "Merge group '%s': Removed %d row(s), added %d merged row(s)",
                group.name,
                len(rows_to_drop),
                len(rows_to_add),
            )
