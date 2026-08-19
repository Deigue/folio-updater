"""Edit command for the folio CLI.

Updates transactions selected either by explicit TxnId or by the same query
terms `folio query` accepts. Changes are expressed with repeatable
`--set Field=value`, where the value is a literal or; for numeric fields - an
arithmetic operation against the row's current value (`Price*=10`)
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import TYPE_CHECKING, Any

import pandas as pd
import typer

from app import bootstrap, get_config
from cli.commands.common import (
    audit_footer,
    backup_folio,
    confirm_selection,
    export_to_parquet,
    resolve_selection,
)
from cli.query_parser import get_valid_column_names
from db.formatters import TransactionFormatter, parse_date
from db.helpers import format_transaction_summary, generate_keys
from db.queries import get_columns, get_connection, get_rows, update_rows
from ui import (
    console_error,
    console_info,
    console_success,
    console_warning,
)
from ui.views.transactions import TransactionDisplay, page_changes
from utils import Column, Table, get_import_logger
from utils.optional_fields import FieldType

if TYPE_CHECKING:
    from cli.selection import Selection

logger = logging.getLogger(__name__)
import_logger = get_import_logger()

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
class SetOperation:
    """A single `--set` instruction, resolved against the Txns table.

    Attributes:
        column: The real column name, e.g. "$" for a `--set Currency=CAD`.
        raw: The original CLI text, used in error messages.
        operator: Arithmetic operator, or None for a literal assignment.
        literal: The literal value, or None for an arithmetic operation.
        operand: The arithmetic operand, or None for a literal assignment.
    """

    column: str
    raw: str
    operator: str | None = None
    literal: str | None = None
    operand: Decimal | None = None

    def describe(self) -> str:
        """Describe the operation the way the user wrote it."""
        return self.raw


def _live_columns() -> list[str]:
    """Get the columns currently present on the Txns table."""
    with get_connection() as conn:
        return get_columns(conn, Table.TXNS)


def _numeric_columns(live_columns: list[str]) -> set[str]:
    """Columns that hold numbers, including configured optional fields."""
    numeric: set[str] = set(NUMERIC_COLUMNS)
    optional_fields = get_config().optional_fields
    numeric.update(
        name
        for name, field in optional_fields.get_all_fields().items()
        if field.field_type == FieldType.NUMERIC and name in live_columns
    )
    return numeric


def _date_columns(live_columns: list[str]) -> set[str]:
    """Columns that hold dates, including configured optional fields."""
    dates: set[str] = set(DATE_COLUMNS)
    optional_fields = get_config().optional_fields
    dates.update(
        name
        for name, field in optional_fields.get_all_fields().items()
        if field.field_type == FieldType.DATE and name in live_columns
    )
    return dates


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
        typer.Exit: If the field is unknown, absent from the table, or must
            not be edited.

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

    # Editing never invents schema the way `folio add --set` does: a column
    # the folio does not have is a mistake, not a new column.
    if column not in live_columns:
        console_error(
            f"'{column}' is not a column in this folio. Editable fields: {known}",
        )
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
    live_columns = _live_columns()
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
                SetOperation(
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

        operations.append(SetOperation(column=column, raw=item, literal=value))

    return operations


def _apply_arithmetic(current: Any, operation: SetOperation, txn_id: int) -> str:
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
            f"Cannot apply '{operation.describe()}' on TxnId {txn_id}: "
            f"current value is empty.",
        )
        raise typer.Exit(1)

    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        console_error(
            f"Cannot apply '{operation.describe()}' on TxnId {txn_id}: "
            f"'{text}' is not a number.",
        )
        raise typer.Exit(1) from None

    # An operator is only ever set together with its operand, per _parse_set_options.
    operand = operation.operand
    if operand is None:
        msg = f"'{operation.describe()}' has no operand."
        raise TypeError(msg)
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
        if operation.operator is None:
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
    formatted, excluded = TransactionFormatter.format_and_validate(edited)

    if not excluded.empty:
        console_error("The edit would produce invalid transaction(s):")
        for _, row in excluded.iterrows():
            txn_id = row.get(Column.Txn.TXN_ID, "?")
            reason = row.get(Column.REJECTION_REASON, "Unknown reason")
            console_error(f"  TxnId {txn_id}: {reason}")
        raise typer.Exit(1)

    return formatted


def _find_conflicts(formatted: pd.DataFrame, edited_ids: set[int]) -> pd.DataFrame:
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
    new_keys = generate_keys(formatted)

    with get_connection() as conn:
        existing = get_rows(conn, Table.TXNS)

    others = existing[~existing[Column.Txn.TXN_ID].astype(int).isin(list(edited_ids))]
    if others.empty:
        return others

    clashing = set(new_keys) & set(generate_keys(others))
    if not clashing:
        return others.iloc[0:0]

    return others[generate_keys(others).isin(list(clashing))]


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


def _update(formatted: pd.DataFrame, set_columns: list[str]) -> int:
    """Write the edited rows, keyed on TxnId.

    Args:
        formatted: The validated, post-edit rows.
        set_columns: Columns to write.

    Raises:
        typer.Exit: If the database rejects the update.

    Returns:
        Number of rows updated.
    """
    payload = formatted[[Column.Txn.TXN_ID, *set_columns]]
    updates = payload.astype(object).where(payload.notna(), None).to_dict("records")

    with get_connection() as conn:
        try:
            return update_rows(
                conn,
                Table.TXNS,
                updates,
                where_columns=[Column.Txn.TXN_ID],
                set_columns=set_columns,
            )
        except sqlite3.IntegrityError as e:
            console_error(f"Database rejected the edit: {e}")
            import_logger.error("EDIT FAILED: %s", e)  # noqa: TRY400
            raise typer.Exit(1) from e


def _log_edit(
    selection: Selection,
    before: pd.DataFrame,
    after: pd.DataFrame,
    changed: list[str],
    operations: list[SetOperation],
) -> None:
    """Record the edit in the shared import audit log.

    Writes both a compact per-field diff and the complete pre- and post-edit
    state, so the folio can be reconstructed from the log alone.
    """
    sets = ", ".join(operation.describe() for operation in operations)
    import_logger.info("EDIT TXN (manual, %s) SET %s", selection.describe(), sets)

    for position in range(len(after)):
        old_row = before.iloc[position]
        new_row = after.iloc[position]
        diffs = "; ".join(
            f"{column} {old_row.get(column)} -> {new_row.get(column)}"
            for column in changed
            if str(old_row.get(column)) != str(new_row.get(column))
        )
        import_logger.info(" ~ TxnId %s: %s", new_row[Column.Txn.TXN_ID], diffs)
        import_logger.info(" - %s", format_transaction_summary(old_row))
        import_logger.info(" + %s", format_transaction_summary(new_row))

    txn_ids = ", ".join(str(txn_id) for txn_id in selection.txn_ids)
    import_logger.info(
        "DONE: %d transaction(s) updated (TxnIds %s)",
        len(after),
        txn_ids,
    )
    audit_footer()


def _confirm_conflicts(conflicts: pd.DataFrame, *, internal: bool) -> bool:
    """Show what the edit would duplicate and ask whether to go ahead."""
    if internal:
        console_warning(
            "This edit would make two of the selected transactions identical.",
        )
    if not conflicts.empty:
        console_warning(
            f"This edit matches {len(conflicts)} existing transaction(s) in the folio:",
        )
        TransactionDisplay().transactions_table(conflicts, max_rows=len(conflicts))

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

    before = selection.transactions.reset_index(drop=True)
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
    duplicating = (not conflicts.empty or internal) and not force
    if duplicating and not _confirm_conflicts(conflicts, internal=internal):
        console_info("No transactions edited.")
        return

    backup_folio()
    updated = _update(after, changed)
    _log_edit(selection, before, after, changed, operations)

    console_success(f"Updated {updated} transaction(s)")
    export_to_parquet()
