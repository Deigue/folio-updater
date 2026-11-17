"""Plans for adding, editing and deleting transactions, checked before any write.

A plan holds the transactions as they are and as they will be, plus any
duplicates the change would create. Working one out never prompts and never
writes; a change the folio cannot take raises `PlanRefusedError` with the reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import TYPE_CHECKING

import pandas as pd

from app import get_config
from db import txns
from domain import TXN_ESSENTIALS, Column
from domain.cells import has_value
from domain.numeric import STORED_DECIMALS
from ingest.dedupe import find_duplicates
from ingest.pipeline import prepare_transactions
from ingest.validation import TransactionFormatter
from models import new_txns_from_frame, txns_frame, txns_from_frame

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from models import NewTxn, Txn

# Division is exact enough for the NUMERIC(20,10) columns it lands in.
DIVISION_PRECISION = 28

# Editing any of these invalidates a settlement date that was calculated from
# them, so the calculator is asked to work it out again.
SETTLEMENT_INPUTS = (
    Column.Txn.TXN_DATE,
    Column.Txn.ACTION,
    Column.Txn.CURRENCY,
)


class PlanRefusedError(Exception):
    """A change the folio cannot take.

    Attributes:
        reason: What is wrong, as one line.
        details: One line per offending transaction, when there are any.
    """

    def __init__(self, reason: str, details: Sequence[str] = ()) -> None:
        """Hold the reason and the per-row details."""
        super().__init__(reason)
        self.reason = reason
        self.details = list(details)


@dataclass(frozen=True)
class Assign:
    """A `--set Field=VALUE` instruction: every row gets the literal.

    Attributes:
        column: The real column name, e.g. "Currency" for `--set currency=CAD`.
        raw: The original CLI text, used in messages and the audit trail.
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
        raw: The original CLI text, used in messages and the audit trail.
        operator: One of `*`, `/`, `+` or `-`.
        operand: The number applied to each row's current value.
    """

    column: str
    raw: str
    operator: str
    operand: Decimal


SetOperation = Assign | Arithmetic


@dataclass(frozen=True)
class AddPlan:
    """One transaction to store.

    Attributes:
        new: The transaction, validated and ready to store.
        duplicates: Stored transactions it matches; while there are any, it is
            not approved and storing it needs the user's go-ahead.
    """

    new: NewTxn
    duplicates: list[Txn]


@dataclass(frozen=True)
class EditPlan:
    """Stored transactions to rewrite.

    Attributes:
        before: The transactions as they are.
        after: The same transactions once edited, in the same order.
        changed: Columns whose value changes for at least one of them.
        duplicates: Other stored transactions an edited one would match.
        makes_identical: Whether the edit makes two of the edited transactions
            identical.
    """

    before: list[Txn]
    after: list[Txn]
    changed: list[str]
    duplicates: list[Txn]
    makes_identical: bool

    @property
    def duplicating(self) -> bool:
        """Whether applying the edit creates a duplicate."""
        return bool(self.duplicates) or self.makes_identical

    def diff(self, old: Txn, new: Txn) -> str:
        """Describe how one transaction changes, field by field: `Price 1 -> 2`."""
        old_row, new_row = old.to_row(), new.to_row()
        return "; ".join(
            f"{column} {old_row.get(column)} -> {new_row.get(column)}"
            for column in self.changed
            if str(old_row.get(column)) != str(new_row.get(column))
        )


@dataclass(frozen=True)
class DeletePlan:
    """Stored transactions to remove.

    Attributes:
        removed: The transactions, as they are.
    """

    removed: list[Txn]


Plan = AddPlan | EditPlan | DeletePlan


def plan_add(fields: Mapping[str, str], *, approve_duplicate: bool) -> AddPlan:
    """Work out the transaction `fields` describe, the way an import would.

    Args:
        fields: Raw values keyed by column name.
        approve_duplicate: Whether a duplicate is approved up front.

    Raises:
        PlanRefusedError: If the transaction is invalid.

    Returns:
        The plan.
    """
    row: dict[str, object] = dict.fromkeys(TXN_ESSENTIALS, pd.NA)
    row.update(fields)
    if approve_duplicate:
        config = get_config()
        row[config.duplicate_approval_column] = config.duplicate_approval_value
    results = prepare_transactions(pd.DataFrame([row]), map_headers=False)

    if not results.invalid_df.empty:
        msg = "Transaction was rejected:"
        raise PlanRefusedError(
            msg,
            [
                str(row.get(Column.REJECTION_REASON, "Unknown reason"))
                for _, row in results.invalid_df.iterrows()
            ],
        )

    rejected = results.db_rejected_df
    if rejected.empty:
        return AddPlan(new=results.approved[0], duplicates=[])
    [new] = new_txns_from_frame(rejected)
    return AddPlan(new=new, duplicates=find_duplicates(rejected, txns.read()).matches)


def plan_edit(selected: Sequence[Txn], operations: Sequence[SetOperation]) -> EditPlan:
    """Work out what `operations` make of the selected transactions.

    Args:
        selected: The transactions being edited, as they are.
        operations: The changes to apply.

    Raises:
        PlanRefusedError: If arithmetic has no number to work from, or an edited
            transaction would be invalid.

    Returns:
        The plan.
    """
    before = txns_frame(selected)
    edited = _apply_operations(before, operations)
    _apply_settlement_rules(edited, operations)

    formatted, invalid = TransactionFormatter.format_and_validate(edited)
    if not invalid.empty:
        msg = "The edit would produce invalid transaction(s):"
        raise PlanRefusedError(
            msg,
            [
                f"TxnId {row.get(Column.Txn.TXN_ID, '?')}: "
                f"{row.get(Column.REJECTION_REASON, 'Unknown reason')}"
                for _, row in invalid.iterrows()
            ],
        )

    after = txns_from_frame(formatted)
    edited_ids = {txn.txn_id for txn in selected}
    others = [txn for txn in txns.read() if txn.txn_id not in edited_ids]
    duplicates = find_duplicates(formatted, others)
    return EditPlan(
        before=list(selected),
        after=after,
        changed=_changed_columns(before, txns_frame(after)),
        duplicates=duplicates.matches,
        makes_identical=bool(duplicates.within.any()),
    )


def _apply_operations(
    selected: pd.DataFrame,
    operations: Sequence[SetOperation],
) -> pd.DataFrame:
    """Return a copy of the transactions with each operation applied, unformatted."""
    edited = selected.reset_index(drop=True).copy()
    for operation in operations:
        if isinstance(operation, Assign):
            edited[operation.column] = operation.literal
            continue
        edited[operation.column] = [
            _compute(
                edited.at[position, operation.column],  # noqa: PD008
                operation,
                int(str(edited.at[position, Column.Txn.TXN_ID])),  # noqa: PD008
            )
            for position in range(len(edited))
        ]
    return edited


def _compute(current: object, operation: Arithmetic, txn_id: int) -> str:
    """Apply an arithmetic operation to a transaction's value, as plain decimal text.

    Kept in Decimal from end to end so that, for example, 17.52 multiplied by
    10 is exactly 175.2 rather than a binary float approximation.

    Raises:
        PlanRefusedError: If the current value is missing or not a number.
    """
    text = str(current).strip() if has_value(current) else ""
    if not text:
        msg = (
            f"Cannot apply '{operation.raw}' on TxnId {txn_id}: current value is empty."
        )
        raise PlanRefusedError(msg)

    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        msg = (
            f"Cannot apply '{operation.raw}' on TxnId {txn_id}: "
            f"'{text}' is not a number."
        )
        raise PlanRefusedError(msg) from None

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
            result = (value / operand).quantize(STORED_DECIMALS)
        result = result.normalize()

    return format(result, "f")


def _apply_settlement_rules(
    edited: pd.DataFrame,
    operations: Sequence[SetOperation],
) -> None:
    """Reconcile settlement dates with the edit, in place.

    A manually supplied settlement date is authoritative and loses the
    "calculated" tag. Otherwise, editing anything the settlement date was
    calculated from makes the stored date stale, so it is cleared and the
    formatter recalculates it. Manually set dates are never touched.
    """
    columns = {operation.column for operation in operations}

    if Column.Txn.SETTLE_DATE in columns:
        edited[Column.Txn.SETTLE_CALCULATED] = 0
        return

    if not columns & set(SETTLEMENT_INPUTS):
        return

    was_calculated = (
        edited[Column.Txn.SETTLE_CALCULATED].astype("boolean").fillna(value=False)
    )
    edited.loc[was_calculated, Column.Txn.SETTLE_DATE] = pd.NA


def _changed_columns(before: pd.DataFrame, after: pd.DataFrame) -> list[str]:
    """List columns whose value changed for at least one transaction, TxnId aside.

    Values are compared as text with missing treated as empty, so a value
    that only round-trips differently through SQLite is not a change.
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
