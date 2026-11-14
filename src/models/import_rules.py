"""Import rules: the transforms ingest applies to source rows before validating them."""

from __future__ import annotations

from typing import Any


class TransformRule:
    """A single transformation rule with conditions and actions."""

    def __init__(
        self,
        conditions: dict[str, list[str]],
        actions: dict[str, Any],
    ) -> None:
        """Initialize a transformation rule.

        Args:
            conditions: Field conditions that must be met (field -> list of values)
            actions: Field transformations to apply (field -> new value)
        """
        self.conditions = conditions
        self.actions = actions

    def __repr__(self) -> str:
        """Return string representation of the rule."""
        return f"TransformRule(conditions={self.conditions}, actions={self.actions})"


class MergeGroup:
    """Configuration for merging groups of related transactions."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        name: str,
        match_fields: list[str],
        source_actions: list[str],
        target_action: str,
        amount_field: str,
        operations: dict[str, Any] | None = None,
    ) -> None:
        """Initialize a merge group configuration.

        Args:
            name: Descriptive name for this merge group
            match_fields: Fields to group by (e.g., TxnDate, Account, Ticker)
            source_actions: Broker actions to merge
                (e.g., ["Dividends", "Withholding Tax"])
            target_action: Final action for the merged row (e.g., "DIVIDEND")
            amount_field: Field containing the amount to sum (e.g., "Amount")
            operations: Additional field operations (e.g., {"Fee": 0})
        """
        self.name = name
        self.match_fields = match_fields
        self.source_actions = source_actions
        self.target_action = target_action
        self.amount_field = amount_field
        self.operations = operations or {}

    def __repr__(self) -> str:
        """Return string representation of the merge group."""
        return (
            f"MergeGroup(name={self.name}, "
            f"match_fields={self.match_fields}, "
            f"source_actions={self.source_actions}, "
            f"target_action={self.target_action})"
        )


class CancelRule:
    """Configuration for rows that cancel an earlier row in the same file.

    A broker that reverses a transaction often sends a second row with the
    opposite amount (IBKR marks it `CANCELLATION`). Each row matching
    `conditions` voids the earliest row on or before it that agrees on every
    `match_fields` value and carries the opposite amount; both are left out.
    """

    def __init__(
        self,
        *,
        name: str,
        conditions: dict[str, list[str]],
        match_fields: list[str],
        amount_field: str,
    ) -> None:
        """Initialize a cancellation rule.

        Args:
            name: Descriptive name for this rule
            conditions: Field conditions marking a cancelling row
            match_fields: Fields the cancelled row must share with it
            amount_field: Field holding the amount, opposite in the cancelled row
        """
        self.name = name
        self.conditions = conditions
        self.match_fields = match_fields
        self.amount_field = amount_field

    def __repr__(self) -> str:
        """Return string representation of the cancellation rule."""
        return (
            f"CancelRule(name={self.name}, conditions={self.conditions}, "
            f"match_fields={self.match_fields})"
        )
