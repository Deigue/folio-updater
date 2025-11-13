"""Account model for Wealthsimple."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from models.base import (
    SerializableModel,
    from_bool_optional,
    from_datetime,
    from_datetime_optional,
    from_int,
    from_list,
    from_str,
    from_str_strict,
    parse_obj,
    to_class,
    to_enum,
)
from utils.constants import Currency


@dataclass
class AccountOwner(SerializableModel):
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
        return AccountOwner(
            account_id=from_str(obj.get("accountId")),
            identity_id=from_str(obj.get("identityId")),
            account_nickname=from_str(obj.get("accountNickname")),
            client_canonical_id=from_str(obj.get("clientCanonicalId")),
            account_opening_agreements_signed=from_bool_optional(
                obj.get("accountOpeningAgreementsSigned"),
            ),
            name=from_str(obj.get("name")),
            email=from_str(obj.get("email")),
            ownership_type=from_str(obj.get("ownershipType")),
            active_invitation=from_str(obj.get("activeInvitation")),
            sent_invitations=from_list(lambda x: x, obj.get("sentInvitations") or []),
            typename=from_str(obj.get("__typename")),
        )


@dataclass
class Money(SerializableModel):
    """Represents a monetary value."""

    amount: str | None
    cents: int
    currency: Currency
    typename: str | None

    @staticmethod
    def from_dict(obj: dict) -> "Money":
        """Create Money from a dictionary."""
        return Money(
            amount=from_str(obj.get("amount")),
            cents=from_int(obj.get("cents")),
            currency=Currency(obj.get("currency")),
            typename=from_str(obj.get("__typename")),
        )


@dataclass
class SimpleReturns(SerializableModel):
    """Simple returns information."""

    amount: Money
    as_of: str | None
    rate: str | None
    reference_date: datetime | None
    typename: str | None

    @staticmethod
    def from_dict(obj: dict) -> "SimpleReturns":
        """Create SimpleReturns from a dictionary."""
        return SimpleReturns(
            amount=parse_obj(Money.from_dict, obj.get("amount")),
            as_of=from_str(obj.get("asOf")),
            rate=from_str(obj.get("rate")),
            reference_date=from_datetime_optional(obj.get("referenceDate")),
            typename=from_str(obj.get("__typename")),
        )


@dataclass
class AccountCurrentFinancials(SerializableModel):
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
        """Create AccountCurrentFinancials from a dictionary."""
        return AccountCurrentFinancials(
            account_id=from_str(obj.get("id")),
            net_liquidation_value=parse_obj(
                Money.from_dict,
                obj.get("netLiquidationValue"),
            ),
            net_deposits=parse_obj(Money.from_dict, obj.get("netDeposits")),
            simple_returns=parse_obj(SimpleReturns.from_dict, obj.get("simpleReturns")),
            total_deposits=parse_obj(Money.from_dict, obj.get("totalDeposits")),
            total_withdrawals=parse_obj(Money.from_dict, obj.get("totalWithdrawals")),
            typename=from_str(obj.get("__typename")),
        )


@dataclass
class AccountFinancials(SerializableModel):
    """Account Financials information."""

    current_combined: AccountCurrentFinancials
    typename: str | None

    @staticmethod
    def from_dict(obj: dict) -> "AccountFinancials":
        """Create AccountFinancials from a dictionary."""
        return AccountFinancials(
            current_combined=parse_obj(
                AccountCurrentFinancials.from_dict,
                obj.get("currentCombined"),
            ),
            typename=from_str(obj.get("__typename")),
        )


@dataclass
class CustodianAccountCurrentFinancialValues(SerializableModel):
    """Custodian account current financial values."""

    deposits: Money
    earnings: Money
    net_deposits: Money
    net_liquidation_value: Money
    withdrawals: Money
    typename: str | None

    @staticmethod
    def from_dict(obj: dict) -> "CustodianAccountCurrentFinancialValues":
        """Create CustodianAccountCurrentFinancialValues from a dictionary."""
        return CustodianAccountCurrentFinancialValues(
            deposits=parse_obj(Money.from_dict, obj.get("deposits")),
            earnings=parse_obj(Money.from_dict, obj.get("earnings")),
            net_deposits=parse_obj(Money.from_dict, obj.get("netDeposits")),
            net_liquidation_value=parse_obj(
                Money.from_dict,
                obj.get("netLiquidationValue"),
            ),
            withdrawals=parse_obj(Money.from_dict, obj.get("withdrawals")),
            typename=from_str(obj.get("__typename")),
        )


@dataclass
class CustodianAccountFinancialsSo(SerializableModel):
    """Custodian account financials SO information."""

    current: CustodianAccountCurrentFinancialValues
    typename: str | None

    @staticmethod
    def from_dict(obj: dict) -> "CustodianAccountFinancialsSo":
        """Create CustodianAccountFinancialsSo from a dictionary."""
        return CustodianAccountFinancialsSo(
            current=parse_obj(
                CustodianAccountCurrentFinancialValues.from_dict,
                obj.get("current"),
            ),
            typename=from_str(obj.get("__typename")),
        )


@dataclass
class CustodianAccount(SerializableModel):
    """Custodian account information."""

    id: str | None
    branch: str | None
    custodian: str | None
    status: str | None
    updated_at: datetime | None
    typename: str | None
    financials: CustodianAccountFinancialsSo

    @staticmethod
    def from_dict(obj: dict) -> "CustodianAccount":
        """Create CustodianAccount from a dictionary."""
        return CustodianAccount(
            id=from_str(obj.get("id")),
            branch=from_str(obj.get("branch")),
            custodian=from_str(obj.get("custodian")),
            status=from_str(obj.get("status")),
            updated_at=from_datetime_optional(obj.get("updatedAt")),
            typename=from_str(obj.get("__typename")),
            financials=parse_obj(
                CustodianAccountFinancialsSo.from_dict,
                obj.get("financials"),
            ),
        )


@dataclass
class Account(SerializableModel):
    """Wealthsimple account data.

    Attributes:
        id: The unique identifier for the account.
    """

    id: str
    archived_at: datetime | None
    branch: str | None
    closed_at: datetime | None
    created_at: datetime
    cache_expired_at: datetime | None
    currency: Currency
    required_identity_verification: str | None
    unified_account_type: str | None
    supported_currencies: list[Currency]
    nickname: str | None
    status: str | None
    account_owner_configuration: str | None
    account_features: list[Any]
    account_owners: list[AccountOwner]
    account_type: str | None
    typename: str | None
    linked_account: str | None
    financials: AccountFinancials
    custodian_accounts: list[CustodianAccount]
    number: str | None
    description: str | None

    @staticmethod
    def from_dict(obj: dict) -> "Account":
        """Create Account from a dictionary."""

        def parse_account_owner(x: Any) -> AccountOwner:  # noqa: ANN401
            if not isinstance(x, dict):
                msg = f"Expected dict in accountOwners list, got {type(x).__name__}"
                raise TypeError(msg)
            return AccountOwner.from_dict(x)

        def parse_custodian_account(x: Any) -> CustodianAccount:  # noqa: ANN401
            if not isinstance(x, dict):
                msg = f"Expected dict in custodianAccounts list, got {type(x).__name__}"
                raise TypeError(msg)
            return CustodianAccount.from_dict(x)

        created_at_raw = obj.get("createdAt")
        if created_at_raw is None:
            msg = "Missing 'createdAt' field in Account dictionary"
            raise ValueError(msg)

        return Account(
            id=from_str_strict(obj.get("id")),
            archived_at=from_datetime_optional(obj.get("archivedAt")),
            branch=from_str(obj.get("branch")),
            closed_at=from_datetime_optional(obj.get("closedAt")),
            created_at=from_datetime(created_at_raw),
            cache_expired_at=from_datetime_optional(obj.get("cacheExpiredAt")),
            currency=Currency(obj.get("currency")),
            required_identity_verification=from_str(
                obj.get("requiredIdentityVerification"),
            ),
            unified_account_type=from_str(obj.get("unifiedAccountType")),
            supported_currencies=from_list(
                lambda x: Currency(x),
                obj.get("supportedCurrencies") or [],
            ),
            nickname=from_str(obj.get("nickname")),
            status=from_str(obj.get("status")),
            account_owner_configuration=from_str(obj.get("accountOwnerConfiguration")),
            account_features=from_list(lambda x: x, obj.get("accountFeatures") or []),
            account_owners=from_list(
                parse_account_owner,
                obj.get("accountOwners") or [],
            ),
            account_type=from_str(obj.get("type")),
            typename=from_str(obj.get("__typename")),
            linked_account=from_str(obj.get("linkedAccount")),
            financials=parse_obj(AccountFinancials.from_dict, obj.get("financials")),
            custodian_accounts=from_list(
                parse_custodian_account,
                obj.get("custodianAccounts") or [],
            ),
            number=from_str(obj.get("number")),
            description=from_str(obj.get("description")),
        )
