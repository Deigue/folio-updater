"""Account model for Wealthsimple."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from models.base import (
    from_bool,
    from_datetime,
    from_int,
    from_list,
    from_str,
    from_str_strict,
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
        result["accountNickname"] = from_str(self.account_nickname)
        result["clientCanonicalId"] = from_str(self.client_canonical_id)
        result["accountOpeningAgreementsSigned"] = from_bool(
            self.account_opening_agreements_signed,
        )
        result["name"] = from_str(self.name)
        result["email"] = from_str(self.email)
        result["ownershipType"] = from_str(self.ownership_type)
        result["activeInvitation"] = from_str(self.active_invitation)
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
        as_of = from_str(obj.get("asOf"))
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
        result["asOf"] = from_str(self.as_of)
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
            msg = (
                "Missing 'totalWithdrawals' field in "
                "AccountCurrentFinancials dictionary"
            )
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
class CustodianAccountCurrentFinancialValues:
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
        if not isinstance(obj, dict):
            msg = f"Expected dict, got {type(obj).__name__}"
            raise TypeError(msg)
        deposits_dict = obj.get("deposits")
        if deposits_dict is None:
            msg = (
                "Missing 'deposits' field in "
                "CustodianAccountCurrentFinancialValues dictionary"
            )
            raise ValueError(msg)
        deposits = Money.from_dict(deposits_dict)

        earnings_dict = obj.get("earnings")
        if earnings_dict is None:
            msg = (
                "Missing 'earnings' field in "
                "CustodianAccountCurrentFinancialValues dictionary"
            )
            raise ValueError(msg)
        earnings = Money.from_dict(earnings_dict)

        net_deposits_dict = obj.get("netDeposits")
        if net_deposits_dict is None:
            msg = (
                "Missing 'netDeposits' field in "
                "CustodianAccountCurrentFinancialValues dictionary"
            )
            raise ValueError(msg)
        net_deposits = Money.from_dict(net_deposits_dict)

        net_liquidation_value_dict = obj.get("netLiquidationValue")
        if net_liquidation_value_dict is None:
            msg = (
                "Missing 'netLiquidationValue' field in "
                "CustodianAccountCurrentFinancialValues dictionary"
            )
            raise ValueError(msg)
        net_liquidation_value = Money.from_dict(net_liquidation_value_dict)

        withdrawals_dict = obj.get("withdrawals")
        if withdrawals_dict is None:
            msg = (
                "Missing 'withdrawals' field in "
                "CustodianAccountCurrentFinancialValues dictionary"
            )
            raise ValueError(msg)
        withdrawals = Money.from_dict(withdrawals_dict)

        typename = from_str(obj.get("__typename"))
        return CustodianAccountCurrentFinancialValues(
            deposits,
            earnings,
            net_deposits,
            net_liquidation_value,
            withdrawals,
            typename,
        )

    def to_dict(self) -> dict:
        """CustodianAccountCurrentFinancialValues to dict."""
        result: dict = {}
        result["deposits"] = to_class(Money, self.deposits)
        result["earnings"] = to_class(Money, self.earnings)
        result["netDeposits"] = to_class(Money, self.net_deposits)
        result["netLiquidationValue"] = to_class(Money, self.net_liquidation_value)
        result["withdrawals"] = to_class(Money, self.withdrawals)
        result["__typename"] = from_str(self.typename)
        return result


@dataclass
class CustodianAccountFinancialsSo:
    """Custodian account financials SO information."""

    current: CustodianAccountCurrentFinancialValues
    typename: str | None

    @staticmethod
    def from_dict(obj: dict) -> "CustodianAccountFinancialsSo":
        """Create CustodianAccountFinancialsSo from a dictionary."""
        if not isinstance(obj, dict):
            msg = f"Expected dict, got {type(obj).__name__}"
            raise TypeError(msg)
        current_dict = obj.get("current")
        if current_dict is None:
            msg = "Missing 'current' field in CustodianAccountFinancialsSo dictionary"
            raise ValueError(msg)
        current = CustodianAccountCurrentFinancialValues.from_dict(current_dict)
        typename = from_str(obj.get("__typename"))
        return CustodianAccountFinancialsSo(current, typename)

    def to_dict(self) -> dict:
        """Convert CustodianAccountFinancialsSo to a dictionary representation."""
        result: dict = {}
        result["current"] = to_class(
            CustodianAccountCurrentFinancialValues,
            self.current,
        )
        result["__typename"] = from_str(self.typename)
        return result


@dataclass
class CustodianAccount:
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
        if not isinstance(obj, dict):
            msg = f"Expected dict, got {type(obj).__name__}"
            raise TypeError(msg)
        account_id = from_str(obj.get("id"))
        branch = from_str(obj.get("branch"))
        custodian = from_str(obj.get("custodian"))
        status = from_str(obj.get("status"))
        updated_at_raw = obj.get("updatedAt")
        updated_at = (
            from_datetime(updated_at_raw) if updated_at_raw is not None else None
        )
        typename = from_str(obj.get("__typename"))
        financials_dict = obj.get("financials")
        if financials_dict is None:
            msg = "Missing 'financials' field in CustodianAccount dictionary"
            raise ValueError(msg)
        financials = CustodianAccountFinancialsSo.from_dict(financials_dict)
        return CustodianAccount(
            account_id,
            branch,
            custodian,
            status,
            updated_at,
            typename,
            financials,
        )

    def to_dict(self) -> dict:
        """Convert CustodianAccount to a dictionary representation."""
        result: dict = {}
        result["id"] = from_str(self.id)
        result["branch"] = from_str(self.branch)
        result["custodian"] = from_str(self.custodian)
        result["status"] = from_str(self.status)
        result["updatedAt"] = (
            self.updated_at.isoformat() if self.updated_at is not None else None
        )
        result["__typename"] = from_str(self.typename)
        result["financials"] = to_class(CustodianAccountFinancialsSo, self.financials)
        return result


@dataclass
class Account:
    """Wealthsimple account data.

    Attributes:
        id (str): The unique identifier for the account.

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
        if not isinstance(obj, dict):
            msg = f"Expected dict, got {type(obj).__name__}"
            raise TypeError(msg)
        account_id = from_str_strict(obj.get("id"))
        archived_at_raw = obj.get("archivedAt")
        archived_at = (
            from_datetime(archived_at_raw) if archived_at_raw is not None else None
        )
        branch = from_str(obj.get("branch"))
        closed_at_raw = obj.get("closedAt")
        closed_at = from_datetime(closed_at_raw) if closed_at_raw is not None else None
        created_at_raw = obj.get("createdAt")
        if created_at_raw is None:
            msg = "Missing 'createdAt' field in Account dictionary"
            raise ValueError(msg)
        created_at = from_datetime(created_at_raw)
        cache_expired_at_raw = obj.get("cacheExpiredAt")
        cache_expired_at = (
            from_datetime(cache_expired_at_raw)
            if cache_expired_at_raw is not None
            else None
        )
        currency = Currency(obj.get("currency"))
        required_identity_verification = from_str(
            obj.get("requiredIdentityVerification"),
        )
        unified_account_type = from_str(obj.get("unifiedAccountType"))
        supported_currencies = from_list(
            lambda x: Currency(x),
            obj.get("supportedCurrencies"),
        )
        nickname = from_str(obj.get("nickname"))
        status = from_str(obj.get("status"))
        account_owner_configuration = from_str(
            obj.get("accountOwnerConfiguration"),
        )
        account_features = from_list(lambda x: x, obj.get("accountFeatures"))

        def parse_account_owner(x) -> AccountOwner:  # noqa: ANN001
            if not isinstance(x, dict):
                msg = f"Expected dict in accountOwners list, got {type(x).__name__}"
                raise TypeError(msg)
            return AccountOwner.from_dict(x)

        account_owners = from_list(
            parse_account_owner,
            obj.get("accountOwners"),
        )
        account_type = from_str(obj.get("type"))
        typename = from_str(obj.get("__typename"))
        linked_account = from_str(obj.get("linkedAccount"))
        financials_dict = obj.get("financials")
        if financials_dict is None:
            msg = "Missing 'financials' field in Account dictionary"
            raise ValueError(msg)
        financials = AccountFinancials.from_dict(financials_dict)

        def parse_custodian_account(x) -> CustodianAccount:  # noqa: ANN001
            if not isinstance(x, dict):
                msg = f"Expected dict in custodianAccounts list, got {type(x).__name__}"
                raise TypeError(msg)
            return CustodianAccount.from_dict(x)

        custodian_accounts = from_list(
            parse_custodian_account,
            obj.get("custodianAccounts"),
        )
        number = from_str(obj.get("number"))
        description = from_str(obj.get("description"))
        return Account(
            account_id,
            archived_at,
            branch,
            closed_at,
            created_at,
            cache_expired_at,
            currency,
            required_identity_verification,
            unified_account_type,
            supported_currencies,
            nickname,
            status,
            account_owner_configuration,
            account_features,
            account_owners,
            account_type,
            typename,
            linked_account,
            financials,
            custodian_accounts,
            number,
            description,
        )

    def to_dict(self) -> dict:
        """Convert the Account instance to a dictionary representation."""
        result: dict = {}
        result["id"] = from_str(self.id)
        result["archivedAt"] = (
            self.archived_at.isoformat() if self.archived_at is not None else None
        )
        result["branch"] = from_str(self.branch)
        result["closedAt"] = (
            self.closed_at.isoformat() if self.closed_at is not None else None
        )
        result["createdAt"] = self.created_at.isoformat()
        result["cacheExpiredAt"] = (
            self.cache_expired_at.isoformat()
            if self.cache_expired_at is not None
            else None
        )
        result["currency"] = to_enum(Currency, self.currency)
        result["requiredIdentityVerification"] = from_str(
            self.required_identity_verification,
        )
        result["unifiedAccountType"] = from_str(self.unified_account_type)
        result["supportedCurrencies"] = from_list(
            lambda x: to_enum(Currency, x),
            self.supported_currencies,
        )
        result["nickname"] = from_str(self.nickname)
        result["status"] = from_str(self.status)
        result["accountOwnerConfiguration"] = from_str(
            self.account_owner_configuration,
        )
        result["accountFeatures"] = from_list(lambda x: x, self.account_features)
        result["accountOwners"] = from_list(
            lambda x: to_class(AccountOwner, x),
            self.account_owners,
        )
        result["type"] = from_str(self.account_type)
        result["__typename"] = from_str(self.typename)
        result["linkedAccount"] = from_str(self.linked_account)
        result["financials"] = to_class(AccountFinancials, self.financials)
        result["custodianAccounts"] = from_list(
            lambda x: to_class(CustodianAccount, x),
            self.custodian_accounts,
        )
        result["number"] = from_str(self.number)
        result["description"] = from_str(self.description)
        return result
