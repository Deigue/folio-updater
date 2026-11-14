"""Transform configuration classes for transaction transformations."""

from __future__ import annotations

from typing import Any

from config.validation import Unusable, report_unknown, report_unusable
from domain import Column
from models.import_rules import CancelRule, MergeGroup, TransformRule
from term import console_warning

# The keys each kind of transforms entry may hold.
_RULE_KEYS = frozenset({"conditions", "actions"})
_MERGE_GROUP_KEYS = frozenset(
    {
        "name",
        "match_fields",
        "source_actions",
        "target_action",
        "amount_field",
        "operations",
    },
)
_CANCEL_RULE_KEYS = frozenset({"name", "conditions", "match_fields", "amount_field"})


def _report_unknown_keys(
    entry: dict[str, Any],
    known: frozenset[str],
    path: str,
) -> None:
    """Report each key of a transforms entry that names nothing it reads."""
    for key in entry:
        if key not in known:
            report_unknown(f"{path}.{key}")


def _refuse_boolean(field: str) -> dict[str, Any]:
    """Warn that a rule field holds a YAML boolean, and give back no fields.

    YAML reads an unquoted `ON`, `OFF`, `YES`, `NO`, `TRUE` or `FALSE` as a
    boolean, so a ticker such as ON would match, or be written as, `True`. A
    rule cannot lose the one field and still mean what was written, so the
    caller drops the whole rule.

    Args:
        field: The rule field that held the boolean.

    Returns:
        An empty mapping, which every caller treats as an unusable rule.
    """
    console_warning(
        f"transforms: ignoring a rule, because {field} holds a YAML boolean "
        "(an unquoted ON, OFF, YES, NO, TRUE or FALSE). Quote the value "
        '(for example "ON") to keep it as text.',
    )
    return {}


def _warn_ignored(what: str, reason: str) -> None:
    """Warn that a transforms entry was dropped, and why."""
    console_warning(f"transforms: ignoring {what}, because {reason}.")


class TransformsConfig:
    """Configuration for transaction transformations."""

    def __init__(self, transforms_config: dict[str, Any] | None = None) -> None:
        """Initialize transforms configuration.

        Args:
            transforms_config: Dictionary containing transformation rules
        """
        self.rules: list[TransformRule] = []
        self.merge_groups: list[MergeGroup] = []
        self.cancellations: list[CancelRule] = []

        if not transforms_config:  # pragma: no cover
            return

        # Each block parses on its own
        self.cancellations = self._parse_cancellations(transforms_config)

        for path, rule_config in self._block_list(transforms_config, "rules"):
            rule = self._parse_rule(rule_config, path)
            if rule:
                self.rules.append(rule)

        for path, group in self._block_list(transforms_config, "merge_groups"):
            merge_group = self._parse_merge_group(group, path)
            if merge_group:
                self.merge_groups.append(merge_group)

    @staticmethod
    def _block_list(
        transforms_config: dict[str, Any],
        key: str,
    ) -> list[tuple[str, Any]]:
        """Read one block's entries, each with its path, such as `transforms.rules[1]`.

        A non-list is ignored with a warning, since the user wrote something
        that is lost. Entries are counted from 1, as they read in the file.
        """
        value = transforms_config.get(key)
        if not isinstance(value, list):
            unusable = Unusable("is not a list")
            report_unusable(f"transforms.{key}", value, unusable, "ignoring it")
            return []
        return [
            (f"transforms.{key}[{number}]", entry)
            for number, entry in enumerate(value, start=1)
        ]

    def _parse_rule(self, rule_config: object, path: str) -> TransformRule | None:
        """Parse a single rule configuration.

        Args:
            rule_config: Raw rule configuration
            path: Where it sits, for warnings.

        Returns:
            Parsed TransformRule or None if invalid
        """
        if isinstance(rule_config, dict):
            _report_unknown_keys(rule_config, _RULE_KEYS, path)
        conditions = (
            rule_config.get("conditions") if isinstance(rule_config, dict) else None
        )
        actions = rule_config.get("actions") if isinstance(rule_config, dict) else None

        if not (
            isinstance(conditions, dict)
            and isinstance(actions, dict)
            and conditions
            and actions
        ):
            _warn_ignored(
                "a rule",
                "it needs conditions and actions, each a mapping with something in it",
            )
            return None

        processed_conditions = self._process_conditions(conditions)
        processed_actions = self._process_actions(actions)

        if processed_conditions and processed_actions:
            return TransformRule(processed_conditions, processed_actions)

        return None  # pragma: no cover

    def _parse_cancellations(
        self,
        transforms_config: dict[str, Any],
    ) -> list[CancelRule]:
        """Parse every valid cancellation rule.

        Args:
            transforms_config: Dictionary containing transformation rules

        Returns:
            The rules that parsed, in the order given.
        """
        cancellations = self._block_list(transforms_config, "cancellations")
        parsed = (self._parse_cancel_rule(entry, path) for path, entry in cancellations)
        return [rule for rule in parsed if rule is not None]

    def _parse_cancel_rule(self, cancel_config: object, path: str) -> CancelRule | None:
        """Parse a single cancellation rule configuration.

        Args:
            cancel_config: Raw cancellation rule configuration
            path: Where it sits, for warnings.

        Returns:
            Parsed CancelRule or None if invalid
        """
        if not isinstance(cancel_config, dict):
            _warn_ignored("a cancellation rule", "it must be a mapping")
            return None
        _report_unknown_keys(cancel_config, _CANCEL_RULE_KEYS, path)

        name = cancel_config.get("name", "")
        raw_conditions = cancel_config.get("conditions")
        conditions = (
            self._process_conditions(raw_conditions)
            if isinstance(raw_conditions, dict)
            else {}
        )
        match_fields = cancel_config.get("match_fields", [])
        amount_field = cancel_config.get("amount_field", Column.Txn.AMOUNT)
        if not all(
            [
                isinstance(name, str) and name,
                conditions,
                isinstance(match_fields, list) and match_fields,
                isinstance(amount_field, str) and amount_field,
            ],
        ):
            _warn_ignored(
                f"cancellation rule {name!r}",
                "it needs a name, conditions, match_fields and an amount_field",
            )
            return None

        return CancelRule(
            name=name,
            conditions=conditions,
            match_fields=[str(f) for f in match_fields],
            amount_field=amount_field,
        )

    def _parse_merge_group(self, group_config: object, path: str) -> MergeGroup | None:
        """Parse a single merge group configuration.

        Args:
            group_config: Raw merge group configuration
            path: Where it sits, for warnings.

        Returns:
            Parsed MergeGroup or None if invalid
        """
        if not isinstance(group_config, dict):
            _warn_ignored("a merge group", "it must be a mapping")
            return None
        _report_unknown_keys(group_config, _MERGE_GROUP_KEYS, path)

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
        ):
            _warn_ignored(
                f"merge group {name!r}",
                "it needs a name, match_fields, at least two source_actions, "
                "a target_action and an amount_field",
            )
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

            if any(isinstance(v, bool) for v in values_list):
                return _refuse_boolean(field)

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

            if isinstance(value, bool):
                return _refuse_boolean(field)
            if value is None:  # pragma: no cover
                processed[field] = ""
            elif isinstance(value, (str, int, float)):
                # Convert all values to strings for consistent storage
                processed[field] = str(value)
            # Skip invalid action values (dict, list, etc.)

        return processed

    def __bool__(self) -> bool:
        """Return True if any rule, merge group or cancellation is configured."""
        return bool(self.rules or self.merge_groups or self.cancellations)

    def __repr__(self) -> str:
        """Return string representation of the configuration."""
        return f"TransformsConfig(rules={self.rules})"
