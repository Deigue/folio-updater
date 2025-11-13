"""Account model for Wealthsimple."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from models.base import (
    from_bool,
    from_datetime,
    from_int,
    from_list,
    from_none,
    from_str,
    to_class,
    to_enum,
)
from utils.constants import Currency


@dataclass
class AccountOwner:
    """Account owner information."""

    account_id: str | None
    identity_id: str | None
    account_nickname: str | None
    client_canonical_id: str | None
    account_opening_agreements_signed: bool | None
    name: str | None
    email: str | None
    ownership_type: str | None
    active_invitation: str | None
    sent_invitations: list[Any]
    typename: str | None

    @staticmethod
    def from_dict(obj: dict) -> "AccountOwner":
        """Create AccountOwner from a dictionary."""
        if not isinstance(obj, dict):
            msg = f"Expected dict, got {type(obj).__name__}"
            raise TypeError(msg)

        account_id = from_str(obj.get("accountId"))
        identity_id = from_str(obj.get("identityId"))
        account_nickname = from_str(obj.get("accountNickname"))
        client_canonical_id = from_str(obj.get("clientCanonicalId"))
        account_opening_agreements_signed = from_bool(
            obj.get("accountOpeningAgreementsSigned"),
        )
        name = from_str(obj.get("name"))
        email = from_str(obj.get("email"))
        ownership_type = from_str(obj.get("ownershipType"))
        active_invitation = from_str(obj.get("activeInvitation"))
        sent_invitations = from_list(lambda x: x, obj.get("sentInvitations"))
        typename = from_str(obj.get("__typename"))
        return AccountOwner(
            account_id,
            identity_id,
            account_nickname,
            client_canonical_id,
            account_opening_agreements_signed,
            name,
            email,
            ownership_type,
            active_invitation,
            sent_invitations,
            typename,
        )

    def to_dict(self) -> dict:
        """Convert AccountOwner to a dictionary representation."""
        result: dict = {}
        result["accountId"] = from_str(self.account_id)
        result["identityId"] = from_str(self.identity_id)
        result["accountNickname"] = from_none(self.account_nickname)
        result["clientCanonicalId"] = from_str(self.client_canonical_id)
        result["accountOpeningAgreementsSigned"] = from_bool(
            self.account_opening_agreements_signed,
        )
        result["name"] = from_str(self.name)
        result["email"] = from_str(self.email)
        result["ownershipType"] = from_str(self.ownership_type)
        result["activeInvitation"] = from_none(self.active_invitation)
        result["sentInvitations"] = from_list(lambda x: x, self.sent_invitations)
        result["__typename"] = from_str(self.typename)
        return result


@dataclass
class Money:
    """Represents a monetary value."""

    amount: str | None
    cents: int
    currency: Currency
    typename: str | None

    @staticmethod
    def from_dict(obj: dict) -> "Money":
        """Create Money from a dictionary."""
        if not isinstance(obj, dict):
            msg = f"Expected dict, got {type(obj).__name__}"
            raise TypeError(msg)
        amount = from_str(obj.get("amount"))
        cents = from_int(obj.get("cents"))
        currency = Currency(obj.get("currency"))
        typename = from_str(obj.get("__typename"))
        return Money(amount, cents, currency, typename)

    def to_dict(self) -> dict:
        """Convert Money to a dictionary representation."""
        result: dict = {}
        result["amount"] = from_str(self.amount)
        result["cents"] = from_int(self.cents)
        result["currency"] = to_enum(Currency, self.currency)
        result["__typename"] = from_str(self.typename)
        return result


@dataclass
class SimpleReturns:
    """Simple returns information."""

    amount: Money
    as_of: str | None
    rate: str | None
    reference_date: datetime
    typename: str | None

    @staticmethod
    def from_dict(obj: dict) -> "SimpleReturns":
        """Create SimpleReturns from a dictionary."""
        if not isinstance(obj, dict):
            msg = f"Expected dict, got {type(obj).__name__}"
            raise TypeError(msg)
        amount_dict = obj.get("amount")
        if amount_dict is None:
            msg = "Missing 'amount' field in SimpleReturns dictionary"
            raise ValueError(msg)
        amount = Money.from_dict(amount_dict)
        as_of = from_none(obj.get("asOf"))
        rate = from_str(obj.get("rate"))
        reference_date_raw = obj.get("referenceDate")
        if reference_date_raw is None:
            msg = "Missing 'referenceDate' field in SimpleReturns dictionary"
            raise ValueError(msg)
        reference_date = from_datetime(reference_date_raw)
        typename = from_str(obj.get("__typename"))
        return SimpleReturns(amount, as_of, rate, reference_date, typename)

    def to_dict(self) -> dict:
        """Convert SimpleReturns to a dictionary representation."""
        result: dict = {}
        result["amount"] = to_class(Money, self.amount)
        result["asOf"] = from_none(self.as_of)
        result["rate"] = from_str(self.rate)
        result["referenceDate"] = self.reference_date.isoformat()
        result["__typename"] = from_str(self.typename)
        return result


@dataclass
class AccountCurrentFinancials:
    """Current financials for an account."""

    account_id: str | None
    net_liquidation_value: Money
    net_deposits: Money
    simple_returns: SimpleReturns
    total_deposits: Money
    total_withdrawals: Money
    typename: str | None

    @staticmethod
    def from_dict(obj: dict) -> "AccountCurrentFinancials":
        """Create CurrentCombined from a dictionary."""
        if not isinstance(obj, dict):
            msg = f"Expected dict, got {type(obj).__name__}"
            raise TypeError(msg)
        account_id = from_str(obj.get("id"))
        net_liquidation_value_dict = obj.get("netLiquidationValue")
        if net_liquidation_value_dict is None:
            msg = (
                "Missing 'netLiquidationValue' field in "
                "AccountCurrentFinancials dictionary"
            )
            raise ValueError(msg)
        net_liquidation_value = Money.from_dict(net_liquidation_value_dict)

        net_deposits_dict = obj.get("netDeposits")
        if net_deposits_dict is None:
            msg = "Missing 'netDeposits' field in AccountCurrentFinancials dictionary"
            raise ValueError(msg)
        net_deposits = Money.from_dict(net_deposits_dict)

        simple_returns_dict = obj.get("simpleReturns")
        if simple_returns_dict is None:
            msg = "Missing 'simpleReturns' field in AccountCurrentFinancials dictionary"
            raise ValueError(msg)
        simple_returns = SimpleReturns.from_dict(simple_returns_dict)

        total_deposits_dict = obj.get("totalDeposits")
        if total_deposits_dict is None:
            msg = "Missing 'totalDeposits' field in AccountCurrentFinancials dictionary"
            raise ValueError(msg)
        total_deposits = Money.from_dict(total_deposits_dict)

        total_withdrawals_dict = obj.get("totalWithdrawals")
        if total_withdrawals_dict is None:
            msg = "Missing 'totalWithdrawals' field in AccountCurrentFinancials dictionary"
            raise ValueError(msg)
        total_withdrawals = Money.from_dict(total_withdrawals_dict)

        typename = from_str(obj.get("__typename"))
        return AccountCurrentFinancials(
            account_id,
            net_liquidation_value,
            net_deposits,
            simple_returns,
            total_deposits,
            total_withdrawals,
            typename,
        )

    def to_dict(self) -> dict:
        """Convert AccountCurrentFinancials to a dictionary representation."""
        result: dict = {}
        result["id"] = from_str(self.account_id)
        result["netLiquidationValue"] = to_class(Money, self.net_liquidation_value)
        result["netDeposits"] = to_class(Money, self.net_deposits)
        result["simpleReturns"] = to_class(SimpleReturns, self.simple_returns)
        result["totalDeposits"] = to_class(Money, self.total_deposits)
        result["totalWithdrawals"] = to_class(Money, self.total_withdrawals)
        result["__typename"] = from_str(self.typename)
        return result


@dataclass
class AccountFinancials:
    """Account Financials information."""

    current_combined: AccountCurrentFinancials
    typename: str | None

    @staticmethod
    def from_dict(obj: dict) -> "AccountFinancials":
        """Create AccountFinancials from a dictionary."""
        if not isinstance(obj, dict):
            msg = f"Expected dict, got {type(obj).__name__}"
            raise TypeError(msg)
        current_combined_dict = obj.get("currentCombined")
        if current_combined_dict is None:
            msg = "Missing 'currentCombined' field in AccountFinancials dictionary"
            raise ValueError(msg)
        current_combined = AccountCurrentFinancials.from_dict(current_combined_dict)
        typename = from_str(obj.get("__typename"))
        return AccountFinancials(current_combined, typename)

    def to_dict(self) -> dict:
        """Convert AccountFinancials to a dictionary representation."""
        result: dict = {}
        result["currentCombined"] = to_class(
            AccountCurrentFinancials,
            self.current_combined,
        )
        result["__typename"] = from_str(self.typename)
        return result


@dataclass
class Account:
    """Wealthsimple account data.

    Attributes:
        id (str): The unique identifier for the account.

    """

    id: str
