"""Transform configuration classes for transaction transformations."""

from __future__ import annotations

from typing import Any

from utils.constants import Column


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

    def __repr__(self) -> str:  # pragma: no cover
        """Return string representation of the rule."""
        return f"TransformRule(conditions={self.conditions}, actions={self.actions})"


class MergeGroup:
    """Configuration for merging groups of related transactions."""

    def __init__(  # noqa: PLR0913
        self,
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
            source_actions: Action types to merge
                (e.g., ["Dividends", "Withholding Tax"])
            target_action: Final action type for merged row (e.g., "DIVIDEND")
            amount_field: Field containing the amount to sum (e.g., "Amount")
            operations: Additional field operations (e.g., {"Fee": 0})
        """
        self.name = name
        self.match_fields = match_fields
        self.source_actions = source_actions
        self.target_action = target_action
        self.amount_field = amount_field
        self.operations = operations or {}

    def __repr__(self) -> str:  # pragma: no cover
        """Return string representation of the merge group."""
        return (
            f"MergeGroup(name={self.name}, "
            f"match_fields={self.match_fields}, "
            f"source_actions={self.source_actions}, "
            f"target_action={self.target_action})"
        )


class TransformsConfig:
    """Configuration for transaction transformations."""

    def __init__(self, transforms_config: dict[str, Any] | None = None) -> None:
        """Initialize transforms configuration.

        Args:
            transforms_config: Dictionary containing transformation rules
        """
        self.rules: list[TransformRule] = []
        self.merge_groups: list[MergeGroup] = []

        if not transforms_config:  # pragma: no cover
            return

        rules_list = transforms_config.get("rules", [])
        if not isinstance(rules_list, list):  # pragma: no cover
            return

        for rule_config in rules_list:
            rule = self._parse_rule(rule_config)
            if rule:
                self.rules.append(rule)

        # Parse merge groups
        merge_groups_list = transforms_config.get("merge_groups", [])
        if isinstance(merge_groups_list, list):
            for group_config in merge_groups_list:
                merge_group = self._parse_merge_group(group_config)
                if merge_group:
                    self.merge_groups.append(merge_group)

    def _parse_rule(self, rule_config: object) -> TransformRule | None:
        """Parse a single rule configuration.

        Args:
            rule_config: Raw rule configuration

        Returns:
            Parsed TransformRule or None if invalid
        """
        if not isinstance(rule_config, dict):  # pragma: no cover
            return None

        conditions = rule_config.get("conditions", {})
        actions = rule_config.get("actions", {})

        if not conditions or not actions:  # pragma: no cover
            return None

        processed_conditions = self._process_conditions(conditions)
        processed_actions = self._process_actions(actions)

        if processed_conditions and processed_actions:
            return TransformRule(processed_conditions, processed_actions)

        return None  # pragma: no cover

    def _parse_merge_group(self, group_config: object) -> MergeGroup | None:
        """Parse a single merge group configuration.

        Args:
            group_config: Raw merge group configuration

        Returns:
            Parsed MergeGroup or None if invalid
        """
        if not isinstance(group_config, dict):  # pragma: no cover
            return None

        name = group_config.get("name", "")
        match_fields = group_config.get("match_fields", [])
        source_actions = group_config.get("source_actions", [])
        target_action = group_config.get("target_action", "")
        amount_field = group_config.get("amount_field", Column.Txn.AMOUNT)
        operations = group_config.get("operations", {})

        # Validate required fields (need at least 2 source actions to merge)
        min_source_actions = 2
        if not all(
            [
                isinstance(name, str) and name,
                isinstance(match_fields, list) and match_fields,
                isinstance(source_actions, list)
                and len(source_actions) >= min_source_actions,
                isinstance(target_action, str) and target_action,
                isinstance(amount_field, str) and amount_field,
            ],
        ):  # pragma: no cover
            return None

        return MergeGroup(
            name=name,
            match_fields=match_fields,
            source_actions=source_actions,
            target_action=target_action,
            amount_field=amount_field,
            operations=operations,
        )

    def _process_conditions(self, conditions: dict[str, Any]) -> dict[str, list[str]]:
        """Process and validate conditions.

        Args:
            conditions: Raw conditions dictionary

        Returns:
            Processed conditions dictionary
        """
        processed = {}
        for field, condition_values in conditions.items():
            if not isinstance(field, str):  # pragma: no cover
                continue

            # Convert single value to list
            if isinstance(condition_values, (str, int, float)):  # pragma: no cover
                values_list = [condition_values]
            elif isinstance(condition_values, list):
                values_list = condition_values
            else:  # pragma: no cover
                continue

            # Convert all values to strings (for consistent comparison)
            string_values = [
                str(v) for v in values_list if isinstance(v, (str, int, float))
            ]

            if string_values:
                processed[field] = string_values

        return processed

    def _process_actions(self, actions: dict[str, Any]) -> dict[str, str]:
        """Process and validate actions.

        Args:
            actions: Raw actions dictionary

        Returns:
            Processed actions dictionary
        """
        processed = {}
        for field, value in actions.items():
            if not isinstance(field, str):  # pragma: no cover
                continue

            if value is None:  # pragma: no cover
                processed[field] = ""
            elif isinstance(value, (str, int, float)):
                # Convert all values to strings for consistent storage
                processed[field] = str(value) if value != "" else ""
            # Skip invalid action values (dict, list, etc.)

        return processed

    def __bool__(self) -> bool:
        """Return True if there are transformation rules or merge groups configured."""
        return bool(self.rules) or bool(self.merge_groups)

    def __len__(self) -> int:  # pragma: no cover
        """Return the number of transformation rules."""
        return len(self.rules)

    def __repr__(self) -> str:  # pragma: no cover
        """Return string representation of the configuration."""
        return f"TransformsConfig(rules={self.rules})"

    def __str__(self) -> str:  # pragma: no cover
        """Return a pretty-printed string representation of the configuration."""
        if not self.rules and not self.merge_groups:
            return "TransformsConfig(no rules or merge groups configured)"

        result = ""

        if self.rules:
            result += f"TransformsConfig({len(self.rules)} rule(s)):\n"
            for i, rule in enumerate(self.rules, 1):
                result += f"    Rule {i}:\n"
                result += f"      Conditions: {rule.conditions}\n"
                result += f"      Actions: {rule.actions}\n"

        if self.merge_groups:
            if result:
                result += "\n"
            result += f"MergeGroups({len(self.merge_groups)} group(s)):\n"
            for i, group in enumerate(self.merge_groups, 1):
                result += f"    Group {i} - {group.name}:\n"
                result += f"      Match Fields: {group.match_fields}\n"
                result += f"      Source Actions: {group.source_actions}\n"
                result += f"      Target Action: {group.target_action}\n"
                result += f"      Amount Field: {group.amount_field}\n"
                if group.operations:
                    result += f"      Operations: {group.operations}\n"

        return result.rstrip()  # Remove trailing newline
