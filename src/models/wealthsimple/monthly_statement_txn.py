"""Monthly Statement Transaction model for Wealthsimple."""

from dataclasses import dataclass
from datetime import datetime

from models.base import SerializableModel, from_datetime_required, from_str


@dataclass
class BrokerageMonthlyStatementTransaction(SerializableModel):
    """Brokerage monthly statement transaction."""

    balance: str | None
    cash_movement: str | None
    unit: str | None
    description: str | None
    transaction_date: datetime
    transaction_type: str | None
    typename: str | None

    @staticmethod
    def from_dict(obj: dict) -> "BrokerageMonthlyStatementTransaction":
        """Create BrokerageMonthlyStatementTransaction from a dictionary."""
        return BrokerageMonthlyStatementTransaction(
            balance=from_str(obj.get("balance")),
            cash_movement=from_str(obj.get("cashMovement")),
            unit=from_str(obj.get("unit")),
            description=from_str(obj.get("description")),
            transaction_date=from_datetime_required(
                obj,
                "transactionDate",
                "BrokerageMonthlyStatementTransaction",
            ),
            transaction_type=from_str(obj.get("transactionType")),
            typename=from_str(obj.get("__typename")),
        )
