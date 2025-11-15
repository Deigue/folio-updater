"""Monthly Statement Transaction model for Wealthsimple."""

from dataclasses import dataclass
from datetime import datetime

from models.base import (
    SerializableModel,
    from_datetime,
    from_str,
    get_last_3_frames,
)


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
        transaction_date_raw = obj.get("transactionDate")
        if transaction_date_raw is None:
            msg = (
                "Missing 'transactionDate' field in "
                "BrokerageMonthlyStatementTransaction dictionary\n"
                f"Call stack:\n{get_last_3_frames()}"
            )
            raise ValueError(msg)

        return BrokerageMonthlyStatementTransaction(
            balance=from_str(obj.get("balance")),
            cash_movement=from_str(obj.get("cashMovement")),
            unit=from_str(obj.get("unit")),
            description=from_str(obj.get("description")),
            transaction_date=from_datetime(transaction_date_raw),
            transaction_type=from_str(obj.get("transactionType")),
            typename=from_str(obj.get("__typename")),
        )
