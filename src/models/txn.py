"""A transaction as `Txns` stores it: `NewTxn` before it has an ID, `Txn` after."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, Self

import pandas as pd

from domain import ACTION_PROFILES, Action, Column, Currency, Impact
from models.cells import (
    read_number,
    read_text,
    required_flag,
    required_number,
    required_text,
    stored_number,
)
from models.columns import Kind, stored

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from decimal import Decimal

# The typed Txns columns. Any other column is an extra one an import added, and
# rides in `extras`.
_TYPED_COLUMNS = frozenset(str(column) for column in Column.Txn)


@dataclass(frozen=True, kw_only=True)
class TxnFields:
    """Every `Txns` column but the ID.

    Attributes:
        txn_date: Transaction date, `YYYY-MM-DD`. Units and cost base follow this.
        action: The transaction's action.
        amount: Total cash amount, sign as stored. None when the transaction
            records none.
        currency: Currency the transaction is denominated in.
        price: Price per unit; units *before* the split on a SPLIT.
        units: Number of units; units *after* the split on a SPLIT.
        fee: Commission. Which sign means "charged" varies by broker, so it is
            detected per account rather than assumed.
        ticker: The security's ticker, as the transaction records it.
        account: Owning account name.
        settle_date: Settlement date, `YYYY-MM-DD`. Cash follows this.
        settle_calculated: Whether the settlement date was calculated from the
            transaction date rather than confirmed from a statement.
        description: Free-text description.
        extras: Columns an import added for source columns no rule maps, keyed by
            column name in table order.
    """

    txn_date: str = field(
        metadata=stored(Column.Txn.TXN_DATE, Kind.DATE, required=True),
    )
    action: Action = field(metadata=stored(Column.Txn.ACTION, required=True))
    amount: Decimal | None = field(metadata=stored(Column.Txn.AMOUNT, Kind.NUMBER))
    currency: Currency = field(metadata=stored(Column.Txn.CURRENCY, required=True))
    price: Decimal | None = field(metadata=stored(Column.Txn.PRICE, Kind.NUMBER))
    units: Decimal | None = field(metadata=stored(Column.Txn.UNITS, Kind.NUMBER))
    fee: Decimal | None = field(metadata=stored(Column.Txn.FEE, Kind.NUMBER))
    ticker: str | None = field(
        metadata=stored(Column.Txn.TICKER, upper=True, non_empty=True),
    )
    account: str = field(
        metadata=stored(Column.Txn.ACCOUNT, required=True, non_empty=True),
    )
    settle_date: str = field(
        metadata=stored(Column.Txn.SETTLE_DATE, Kind.DATE, required=True),
    )
    settle_calculated: bool = field(
        metadata=stored(Column.Txn.SETTLE_CALCULATED, Kind.FLAG, required=True),
    )
    description: str | None = field(
        default=None,
        metadata=stored(Column.Txn.DESCRIPTION),
    )
    extras: Mapping[str, str | None] = field(default_factory=dict)

    @property
    def fx_date(self) -> str:
        """Date whose rate converts this transaction: its settlement date."""
        return self.settle_date

    @property
    def impact(self) -> Impact:
        """What this transaction does to the cost base."""
        return ACTION_PROFILES[self.action].impact

    @property
    def is_in_kind_transfer(self) -> bool:
        """Whether a transfer leg moves units rather than cash."""
        return bool(self.ticker) and bool(self.units)

    def to_row(self) -> dict[str, object]:
        """Return the row this transaction is stored as, keyed by column name."""
        row: dict[str, object] = {
            str(Column.Txn.TXN_DATE): self.txn_date,
            str(Column.Txn.ACTION): str(self.action),
            str(Column.Txn.AMOUNT): stored_number(self.amount),
            str(Column.Txn.CURRENCY): str(self.currency),
            str(Column.Txn.PRICE): stored_number(self.price),
            str(Column.Txn.UNITS): stored_number(self.units),
            str(Column.Txn.FEE): stored_number(self.fee),
            str(Column.Txn.TICKER): self.ticker,
            str(Column.Txn.ACCOUNT): self.account,
            str(Column.Txn.SETTLE_DATE): self.settle_date,
            str(Column.Txn.SETTLE_CALCULATED): int(self.settle_calculated),
            str(Column.Txn.DESCRIPTION): self.description,
        }
        row.update(self.extras)
        return row

    @staticmethod
    def _read(row: Mapping[str, object]) -> dict[str, object]:
        """Read every column but the ID out of a stored row.

        Raises:
            ValueError: If a column the transaction cannot do without is empty or
                holds a value folio does not know.
        """
        columns = Column.Txn
        return {
            "txn_date": required_text(row.get(columns.TXN_DATE), columns.TXN_DATE),
            "action": Action(required_text(row.get(columns.ACTION), columns.ACTION)),
            "amount": read_number(row.get(columns.AMOUNT)),
            "currency": Currency(
                required_text(row.get(columns.CURRENCY), columns.CURRENCY),
            ),
            "price": read_number(row.get(columns.PRICE)),
            "units": read_number(row.get(columns.UNITS)),
            "fee": read_number(row.get(columns.FEE)),
            "ticker": read_text(row.get(columns.TICKER)),
            "account": required_text(row.get(columns.ACCOUNT), columns.ACCOUNT),
            "settle_date": required_text(
                row.get(columns.SETTLE_DATE),
                columns.SETTLE_DATE,
            ),
            "settle_calculated": required_flag(
                row.get(columns.SETTLE_CALCULATED),
                columns.SETTLE_CALCULATED,
            ),
            "description": read_text(row.get(columns.DESCRIPTION)),
            "extras": {
                str(column): read_text(value)
                for column, value in row.items()
                if str(column) not in _TYPED_COLUMNS
            },
        }


@dataclass(frozen=True, kw_only=True)
class NewTxn(TxnFields):
    """A transaction not stored yet, so with no ID."""

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> Self:
        """Read a transaction out of a row, ignoring any ID it carries.

        Args:
            row: The row, keyed by column name.

        Returns:
            The transaction.
        """
        return cls(**cls._read(row))  # ty: ignore[invalid-argument-type]

    def stored_as(self, txn_id: int) -> Txn:
        """Return this transaction as stored under `txn_id`.

        Args:
            txn_id: The ID it was stored under.

        Returns:
            The stored transaction, every field unchanged.
        """
        fields_of = {item.name: getattr(self, item.name) for item in fields(TxnFields)}
        return Txn(txn_id=txn_id, **fields_of)


@dataclass(frozen=True, kw_only=True)
class Txn(TxnFields):
    """A stored transaction.

    Attributes:
        txn_id: Primary key, given by SQLite when the transaction was stored.
    """

    txn_id: int = field(
        metadata=stored(Column.Txn.TXN_ID, Kind.INTEGER, key=True, autoincrement=True),
    )

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> Self:
        """Read a stored transaction out of its row.

        Args:
            row: The row, keyed by column name.

        Returns:
            The transaction.
        """
        txn_id = required_number(row.get(Column.Txn.TXN_ID), Column.Txn.TXN_ID)
        return cls(txn_id=int(txn_id), **cls._read(row))  # ty: ignore[invalid-argument-type]

    def to_row(self) -> dict[str, object]:
        """Return the row this transaction is stored as, keyed by column name."""
        return {str(Column.Txn.TXN_ID): self.txn_id, **super().to_row()}


def txns_frame(txns: Sequence[Txn]) -> pd.DataFrame:
    """Lay stored transactions out as a frame, in table columns."""
    return pd.DataFrame([_frame_row(txn) for txn in txns])


def _frame_row(txn: Txn) -> dict[str, object]:
    """Return a transaction's row with its numbers as numbers."""
    row = txn.to_row()
    for column, value in (
        (Column.Txn.AMOUNT, txn.amount),
        (Column.Txn.PRICE, txn.price),
        (Column.Txn.UNITS, txn.units),
        (Column.Txn.FEE, txn.fee),
    ):
        row[str(column)] = _frame_number(value)
    return row


def _frame_number(value: Decimal | None) -> int | float | None:
    """Give a number the type SQLite holds it as: a whole one is an integer."""
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)
