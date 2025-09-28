"""Transform configuration classes for transaction transformations."""

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


class TransformsConfig:
    """Configuration for transaction transformations."""

    def __init__(self, transforms_config: dict[str, Any] | None = None) -> None:
        """Initialize transforms configuration.

        Args:
            transforms_config: Dictionary containing transformation rules
        """
        self.rules: list[TransformRule] = []

        if not transforms_config:
            return

        rules_list = transforms_config.get("rules", [])
        if not isinstance(rules_list, list):
            return

        for rule_config in rules_list:
            rule = self._parse_rule(rule_config)
            if rule:
                self.rules.append(rule)

    def _parse_rule(self, rule_config: object) -> TransformRule | None:
        """Parse a single rule configuration.

        Args:
            rule_config: Raw rule configuration

        Returns:
            Parsed TransformRule or None if invalid
        """
        if not isinstance(rule_config, dict):
            return None

        conditions = rule_config.get("conditions", {})
        actions = rule_config.get("actions", {})

        if not conditions or not actions:
            return None

        processed_conditions = self._process_conditions(conditions)
        processed_actions = self._process_actions(actions)

        if processed_conditions and processed_actions:
            return TransformRule(processed_conditions, processed_actions)

        return None

    def _process_conditions(self, conditions: dict[str, Any]) -> dict[str, list[str]]:
        """Process and validate conditions.

        Args:
            conditions: Raw conditions dictionary

        Returns:
            Processed conditions dictionary
        """
        processed = {}
        for field, condition_values in conditions.items():
            if not isinstance(field, str):
                continue

            # Convert single value to list
            if isinstance(condition_values, (str, int, float)):
                values_list = [condition_values]
            elif isinstance(condition_values, list):
                values_list = condition_values
            else:
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
            if not isinstance(field, str):
                continue

            # Convert None to empty string
            if value is None:
                processed[field] = ""
            elif isinstance(value, (str, int, float)):
                # Convert all values to strings for consistent storage
                processed[field] = str(value) if value != "" else ""
            # Skip invalid action values (dict, list, etc.)

        return processed

    def __bool__(self) -> bool:
        """Return True if there are transformation rules configured."""
        return bool(self.rules)

    def __len__(self) -> int:
        """Return the number of transformation rules."""
        return len(self.rules)

    def __repr__(self) -> str:
        """Return string representation of the configuration."""
        return f"TransformsConfig(rules={self.rules})"

    def __str__(self) -> str:
        """Return a pretty-printed string representation of the configuration."""
        if not self.rules:
            return "TransformsConfig(no rules configured)"

        result = f"TransformsConfig({len(self.rules)} rule(s)):\n"
        for i, rule in enumerate(self.rules, 1):
            result += f"    Rule {i}:\n"
            result += f"      Conditions: {rule.conditions}\n"
            result += f"      Actions: {rule.actions}\n"
        return result.rstrip()  # Remove trailing newline
