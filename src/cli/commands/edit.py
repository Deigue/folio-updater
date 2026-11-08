"""Edit command for the folio CLI.

Updates transactions selected either by explicit TxnId or by the same query
terms `folio query` accepts. Changes are expressed with repeatable
`--set Field=value`, where the value is a literal or; for numeric fields - an
arithmetic operation against the row's current value (`Price*=10`)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import TYPE_CHECKING, Any

import pandas as pd
import typer

from app import audit, bootstrap, get_config
from app.audit import AuditMark
from cli.commands.common import (
    confirm_selection,
    export_to_parquet,
    resolve_selection,
)
from cli.query_parser import get_valid_column_names
from config.optional_fields import FieldType
from db import IntegrityError, current_backup, txns
from db.helpers import format_transaction_summary, generate_keys, txns_with_keys
from domain import Column
from ingest.validation import TransactionFormatter, parse_date
from models import txns_frame, txns_from_frame
from term import console_error, console_info, console_success, console_warning
from ui.transaction_table import page_changes, show_transactions

if TYPE_CHECKING:
    from app.audit import AuditEntry
    from models import Txn

logger = logging.getLogger(__name__)

ARITHMETIC_OPERATORS = "*/+-"

# Division is exact enough for the NUMERIC(20,10) columns it lands in.
DIVISION_PRECISION = 28
DIVISION_DECIMALS = Decimal("0.0000000001")

# Derived or identifying columns that an edit must not set directly.
PROTECTED_COLUMNS = (Column.Txn.TXN_ID, Column.Txn.SETTLE_CALCULATED)

# Columns whose stored value is numeric, and so accept arithmetic.
NUMERIC_COLUMNS = (
    Column.Txn.AMOUNT,
    Column.Txn.PRICE,
    Column.Txn.UNITS,
    Column.Txn.FEE,
)

DATE_COLUMNS = (Column.Txn.TXN_DATE, Column.Txn.SETTLE_DATE)

# Editing any of these invalidates a settlement date that was calculated from
# them, so the calculator is asked to work it out again.
SETTLEMENT_INPUTS = (
    Column.Txn.TXN_DATE,
    Column.Txn.ACTION,
    Column.Txn.CURRENCY,
)


@dataclass(frozen=True)
class Assign:
    """A `--set Field=VALUE` instruction: every row gets the literal.

    Attributes:
        column: The real column name, e.g. "Currency" for `--set currency=CAD`.
        raw: The original CLI text, used in error messages.
        literal: The value to store.
    """

    column: str
    raw: str
    literal: str


@dataclass(frozen=True)
class Arithmetic:
    """A `--set Field*=N` instruction: each row's value is worked out from its own.

    Attributes:
        column: The real column name.
        raw: The original CLI text, used in error messages.
        operator: One of `ARITHMETIC_OPERATORS`.
        operand: The number applied to each row's current value.
    """

    column: str
    raw: str
    operator: str
    operand: Decimal


SetOperation = Assign | Arithmetic


def _numeric_columns(live_columns: list[str]) -> set[str]:
    """Columns that hold numbers, including configured optional fields."""
    optional_fields = get_config().optional_fields
    return {
        *NUMERIC_COLUMNS,
        *optional_fields.columns_of_type(FieldType.NUMERIC, live_columns),
    }


def _date_columns(live_columns: list[str]) -> set[str]:
    """Columns that hold dates, including configured optional fields."""
    optional_fields = get_config().optional_fields
    return {
        *DATE_COLUMNS,
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


def _apply_arithmetic(current: Any, operation: Arithmetic, txn_id: int) -> str:
    """Apply an arithmetic `--set` to a row's current value.

    Kept in Decimal from end to end so that, for example, 17.52 multiplied by
    10 is exactly 175.2 rather than a binary float approximation.

    Args:
        current: The row's existing value for the column.
        operation: The arithmetic operation to apply.
        txn_id: TxnId of the row, for the error message.

    Raises:
        typer.Exit: If the current value is missing or not a number.

    Returns:
        The new value as a plain decimal string.
    """
    text = "" if current is None or pd.isna(current) else str(current).strip()
    if not text:
        console_error(
            f"Cannot apply '{operation.raw}' on TxnId {txn_id}: "
            f"current value is empty.",
        )
        raise typer.Exit(1)

    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        console_error(
            f"Cannot apply '{operation.raw}' on TxnId {txn_id}: "
            f"'{text}' is not a number.",
        )
        raise typer.Exit(1) from None

    operand = operation.operand
    if operation.operator == "*":
        result = value * operand
    elif operation.operator == "+":
        result = value + operand
    elif operation.operator == "-":
        result = value - operand
    else:
        with localcontext() as context:
            context.prec = DIVISION_PRECISION
            result = (value / operand).quantize(DIVISION_DECIMALS)
        result = result.normalize()

    return format(result, "f")


def _build_edited(
    selected: pd.DataFrame,
    operations: list[SetOperation],
) -> pd.DataFrame:
    """Apply the `--set` operations to the selected rows.

    Args:
        selected: The transactions being edited, as they are now.
        operations: The operations to apply.

    Returns:
        A copy of the rows with the new values, before formatting.
    """
    edited = selected.reset_index(drop=True).copy()
    for operation in operations:
        if isinstance(operation, Assign):
            edited[operation.column] = operation.literal
            continue
        edited[operation.column] = [
            _apply_arithmetic(
                edited.at[position, operation.column],  # noqa: PD008
                operation,
                int(str(edited.at[position, Column.Txn.TXN_ID])),  # noqa: PD008
            )
            for position in range(len(edited))
        ]
    return edited


def _apply_settlement_rules(
    edited: pd.DataFrame,
    operations: list[SetOperation],
) -> None:
    """Reconcile settlement dates with the edit, in place.

    A manually supplied settlement date is authoritative and loses the
    "calculated" tag. Otherwise, editing anything the settlement date was
    calculated from makes the stored date stale, so it is cleared and the
    formatter recalculates it. Manually set dates are never touched.

    Args:
        edited: The rows with `--set` values applied.
        operations: The operations that were applied.
    """
    columns = {operation.column for operation in operations}

    if Column.Txn.SETTLE_DATE in columns:
        edited[Column.Txn.SETTLE_CALCULATED] = 0
        return

    if not columns & set(SETTLEMENT_INPUTS):
        return

    was_calculated = (
        edited[Column.Txn.SETTLE_CALCULATED]
        .astype("boolean")
        .fillna(
            value=False,
        )
    )
    edited.loc[was_calculated, Column.Txn.SETTLE_DATE] = pd.NA


def _validate(edited: pd.DataFrame) -> pd.DataFrame:
    """Re-validate edited rows the way an import would.

    Args:
        edited: The rows with `--set` values applied.

    Raises:
        typer.Exit: If any row would be rejected.

    Returns:
        The formatted rows.
    """
    formatted, invalid = TransactionFormatter.format_and_validate(edited)

    if not invalid.empty:
        console_error("The edit would produce invalid transaction(s):")
        for _, row in invalid.iterrows():
            txn_id = row.get(Column.Txn.TXN_ID, "?")
            reason = row.get(Column.REJECTION_REASON, "Unknown reason")
            console_error(f"  TxnId {txn_id}: {reason}")
        raise typer.Exit(1)

    return formatted


def _find_conflicts(formatted: pd.DataFrame, edited_ids: set[int]) -> list[Txn]:
    """Find transactions the edited rows would collide with.

    Checks the edited rows against each other and against every row in the
    folio except the ones being edited, since those are about to be replaced.
    Excluding them matters: an edit that leaves the essential fields alone
    would otherwise be reported as a duplicate of its own pre-edit self.

    Args:
        formatted: The validated, post-edit rows.
        edited_ids: TxnIds of the rows being edited.

    Returns:
        Existing transactions that clash, empty if there are none.
    """
    others = [txn for txn in txns.read() if txn.txn_id not in edited_ids]
    return txns_with_keys(others, set(generate_keys(formatted)))


def _has_internal_duplicates(formatted: pd.DataFrame) -> bool:
    """Whether the edit would make two of the edited rows identical."""
    return bool(generate_keys(formatted).duplicated().any())


def _changed_columns(before: pd.DataFrame, after: pd.DataFrame) -> list[str]:
    """List columns whose value changed for at least one row.

    Values are compared as text with missing treated as empty, so a value
    that only round-trips differently through SQLite is not a change.

    Args:
        before: The rows as they are now.
        after: The rows once the edit is applied.

    Returns:
        Changed column names, excluding TxnId.
    """

    def normalize(frame: pd.DataFrame, column: str) -> pd.Series:
        return frame[column].astype(object).where(frame[column].notna(), "").astype(str)

    return [
        column
        for column in after.columns
        if column != Column.Txn.TXN_ID
        and column in before.columns
        and not normalize(before, column).equals(normalize(after, column))
    ]


def _write_edits(formatted: pd.DataFrame, entry: AuditEntry) -> int:
    """Write the edited rows, keyed on TxnId.

    Args:
        formatted: The validated, post-edit rows.
        entry: The audit entry the edit is recorded in.

    Raises:
        typer.Exit: If the database rejects the update.

    Returns:
        Number of rows updated.
    """
    try:
        return txns.update(txns_from_frame(formatted))
    except IntegrityError as e:
        console_error(f"Database rejected the edit: {e}")
        entry.failed(f"the table refused the edit: {e}")
        raise typer.Exit(1) from e


def _log_updated(
    before: pd.DataFrame,
    after: pd.DataFrame,
    changed: list[str],
) -> None:
    """Write the UPDATE stage: each change field by field, then the old and new row.

    Both versions of the row are kept, so the folio can be reconstructed from the
    log alone.

    Args:
        before: The matched rows as they stood.
        after: The matched rows as they were meant to end up.
        changed: Columns the edit touches.
    """
    audit.stage(
        "UPDATE %d transaction(s); %s",
        len(after),
        audit.describe_backup(current_backup()),
    )
    for position in range(len(after)):
        old_row = before.iloc[position]
        new_row = after.iloc[position]
        diffs = "; ".join(
            f"{column} {old_row.get(column)} -> {new_row.get(column)}"
            for column in changed
            if str(old_row.get(column)) != str(new_row.get(column))
        )
        audit.row(AuditMark.CHANGED, f"TxnId {new_row[Column.Txn.TXN_ID]}: {diffs}")
        audit.row(AuditMark.REMOVED, format_transaction_summary(old_row), depth=3)
        audit.row(AuditMark.STORED, format_transaction_summary(new_row), depth=3)


def _confirm_conflicts(conflicts: list[Txn], *, internal: bool) -> bool:
    """Show what the edit would duplicate and ask whether to go ahead."""
    if internal:
        console_warning(
            "This edit would make two of the selected transactions identical.",
        )
    if conflicts:
        console_warning(
            f"This edit matches {len(conflicts)} existing transaction(s) in the folio:",
        )
        show_transactions(conflicts, max_rows=len(conflicts))

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
    bootstrap.reload_config()

    operations = _parse_set_options(set_values)
    selection = resolve_selection(terms, verb="edit")

    before = txns_frame(selection.transactions)
    edited = _build_edited(before, operations)
    _apply_settlement_rules(edited, operations)
    after = _validate(edited)

    changed = _changed_columns(before, after)
    if not changed:
        console_info("No changes to apply.")
        return

    page_changes(before, after, changed, title="Pending Changes")

    if dry_run:
        console_info("Dry run - nothing was written to the folio.")
        return

    if not confirm_selection(len(after), verb="edit", force=force):
        console_info("No transactions edited.")
        return

    conflicts = _find_conflicts(after, set(selection.txn_ids))
    internal = _has_internal_duplicates(after)
    duplicating = (bool(conflicts) or internal) and not force
    if duplicating and not _confirm_conflicts(conflicts, internal=internal):
        console_info("No transactions edited.")
        return

    sets = ", ".join(operation.raw for operation in operations)
    with audit.entry("EDIT TXNS %s SET %s", selection.describe(), sets) as entry:
        updated = _write_edits(after, entry)
        _log_updated(before, after, changed)
        if updated == len(after):
            entry.done("%d updated", updated)
        else:
            entry.failed(f"{len(after)} transaction(s) matched but {updated} updated")

    if updated != len(after):
        console_error(
            f"Updated {updated} of {len(after)} transaction(s); "
            "the folio is unchanged for the rest. See the log for details.",
        )
        raise typer.Exit(1)

    console_success(f"Updated {updated} transaction(s)")
    export_to_parquet()
