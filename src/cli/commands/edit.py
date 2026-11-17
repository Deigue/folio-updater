"""Edit command for the folio CLI.

Updates transactions selected either by explicit TxnId or by the same query
terms `folio query` accepts. Changes are expressed with repeatable
`--set Field=value`, where the value is a literal or; for numeric fields - an
arithmetic operation against the row's current value (`Price*=10`)
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

import typer

from app import audit, get_config
from cli.commands.common import (
    commit,
    confirm_selection,
    refusal,
    resolve_selection,
)
from cli.query_parser import get_valid_column_names
from db import txns
from domain import Column
from ingest.changes import (
    Arithmetic,
    Assign,
    EditPlan,
    PlanRefusedError,
    SetOperation,
    plan_edit,
)
from ingest.validation import parse_date
from models import TxnFields, txns_frame
from models.columns import Kind, columns_of_kind
from models.optional_field import FieldType
from term import console_error, console_info, console_warning
from ui.transaction_table import page_changes, show_transactions

if TYPE_CHECKING:
    from models import Txn

ARITHMETIC_OPERATORS = "*/+-"

# Derived or identifying columns that an edit must not set directly.
PROTECTED_COLUMNS = (Column.Txn.TXN_ID, Column.Txn.SETTLE_CALCULATED)


def _numeric_columns(live_columns: list[str]) -> set[str]:
    """Columns that hold numbers, including configured optional fields."""
    optional_fields = get_config().optional_fields
    return {
        *columns_of_kind(TxnFields, Kind.NUMBER),
        *optional_fields.columns_of_type(FieldType.NUMERIC, live_columns),
    }


def _date_columns(live_columns: list[str]) -> set[str]:
    """Columns that hold dates, including configured optional fields."""
    optional_fields = get_config().optional_fields
    return {
        *columns_of_kind(TxnFields, Kind.DATE),
        *optional_fields.columns_of_type(FieldType.DATE, live_columns),
    }


def _split_set_value(item: str) -> tuple[str, str, str | None]:
    """Split a `--set` argument into field, value and arithmetic operator.

    Args:
        item: Raw `--set` text, e.g. "Price*=10" or "Price=175.20".

    Raises:
        typer.Exit: If the argument is not in Field=VALUE form.

    Returns:
        Tuple of (field, value, operator) where operator is None for a
        literal assignment.
    """
    equals = item.find("=")
    operator: str | None = None
    field_end = equals

    # An operator only counts immediately before the first "=", so a literal
    # containing "*=" cannot be mistaken for an operation.
    if equals > 0 and item[equals - 1] in ARITHMETIC_OPERATORS:
        operator = item[equals - 1]
        field_end = equals - 1

    field = item[:field_end].strip()
    if equals < 0 or not field:
        console_error(f"Invalid --set value '{item}'. Expected Field=VALUE.")
        raise typer.Exit(1)

    value = item[equals + 1 :]
    return field, value.strip() if operator else value, operator


def _resolve_column(
    field: str,
    valid_columns: dict[str, str],
    live_columns: list[str],
    raw: str,
) -> str:
    """Resolve a user-supplied field name to a real Txns column.

    Args:
        field: Field name as typed.
        valid_columns: Lowercase alias to real column mapping.
        live_columns: Columns actually present on the Txns table.
        raw: The original `--set` text, for the error message.

    Raises:
        typer.Exit: If the field is unknown or must not be edited.

    Returns:
        The real column name.
    """
    column = valid_columns.get(field.lower())
    editable = set(valid_columns.values()) & set(live_columns)
    known = ", ".join(sorted(editable - set(PROTECTED_COLUMNS)))
    if column is None:
        console_error(f"Unknown field '{field}' in '{raw}'. Editable fields: {known}")
        raise typer.Exit(1)

    if column in PROTECTED_COLUMNS:
        console_error(f"'{column}' cannot be edited directly.")
        raise typer.Exit(1)

    return column


def _parse_set_options(values: list[str]) -> list[SetOperation]:
    """Parse repeated `--set` options into resolved operations.

    Args:
        values: Raw `--set` arguments.

    Raises:
        typer.Exit: On a malformed argument, an unknown or protected field, a
            repeated field, arithmetic on a non-numeric field, or an invalid
            date literal.

    Returns:
        The parsed operations, in the order given.
    """
    live_columns = txns.columns()
    valid_columns = get_valid_column_names(live_columns)
    numeric_columns = _numeric_columns(live_columns)
    date_columns = _date_columns(live_columns)

    operations: list[SetOperation] = []
    seen: set[str] = set()

    for item in values:
        field, value, operator = _split_set_value(item)
        column = _resolve_column(field, valid_columns, live_columns, item)

        if column in seen:
            console_error(f"Field '{column}' was given more than once.")
            raise typer.Exit(1)
        seen.add(column)

        if operator is not None:
            if column not in numeric_columns:
                console_error(
                    f"Cannot apply '{operator}={value}' to {column}: "
                    f"not a numeric field.",
                )
                raise typer.Exit(1)
            try:
                operand = Decimal(value)
            except (InvalidOperation, ValueError):
                console_error(f"'{value}' in '{item}' is not a number.")
                raise typer.Exit(1) from None
            if operator == "/" and operand == 0:
                console_error(f"Cannot divide {column} by zero.")
                raise typer.Exit(1)
            operations.append(
                Arithmetic(
                    column=column,
                    raw=item,
                    operator=operator,
                    operand=operand,
                ),
            )
            continue

        # A date literal that the formatter would treat as optional would be
        # silently recalculated rather than rejected, so validate it here.
        if column in date_columns and value and parse_date(value) is None:
            console_error(f"'{value}' in '{item}' is not a valid date.")
            raise typer.Exit(1)

        operations.append(Assign(column=column, raw=item, literal=value))

    return operations


def _plan(selected: list[Txn], operations: list[SetOperation]) -> EditPlan:
    """Work out the edit, or report why it is refused and stop.

    Raises:
        typer.Exit: If the plan is refused.
    """
    try:
        return plan_edit(selected, operations)
    except PlanRefusedError as refused:
        raise refusal(refused) from refused


def _confirm_duplicates(plan: EditPlan) -> bool:
    """Show what the edit would duplicate and ask whether to go ahead."""
    if plan.makes_identical:
        console_warning(
            "This edit would make two of the selected transactions identical.",
        )
    if plan.duplicates:
        console_warning(
            f"This edit matches {len(plan.duplicates)} existing transaction(s) "
            "in the folio:",
        )
        show_transactions(plan.duplicates, max_rows=len(plan.duplicates))

    return typer.confirm(
        "Apply anyway and create duplicate transaction(s)?",
        default=False,
    )


def edit_transactions(
    terms: list[str],
    set_values: list[str],
    *,
    force: bool = False,
    dry_run: bool = False,
) -> None:
    """Edit transactions in the folio.

    Args:
        terms: TxnIds, or query terms using the `folio query` syntax.
        set_values: Repeated Field=VALUE changes, literal or arithmetic.
        force: Apply without confirming, and allow duplicates.
        dry_run: Preview the before/after without writing.
    """
    operations = _parse_set_options(set_values)
    selection = resolve_selection(terms, verb="edit")
    plan = _plan(selection.transactions, operations)

    if not plan.changed:
        console_info("No changes to apply.")
        return

    page_changes(
        txns_frame(plan.before),
        txns_frame(plan.after),
        plan.changed,
        title="Pending Changes",
    )

    if dry_run:
        console_info("Dry run - nothing was written to the folio.")
        return

    if not confirm_selection(len(plan.after), verb="edit", force=force):
        console_info("No transactions edited.")
        return

    if plan.duplicating and not force and not _confirm_duplicates(plan):
        console_info("No transactions edited.")
        return

    sets = ", ".join(operation.raw for operation in operations)
    with audit.entry("EDIT TXNS %s SET %s", selection.describe(), sets) as entry:
        commit(plan, entry)
