"""Activity Feed Item from Wealthsimple."""

from dataclasses import dataclass
from datetime import datetime

import dateutil.parser


def from_str(x: str | None) -> str | None:
    """Validate and return a string value or None.

    Args:
        x: The value to validate as a string or None.

    Returns:
        The input string value or None.

    Raises:
        TypeError: If x is not a string or None.
    """
    if x is not None and not isinstance(x, str):
        msg = f"Expected str or None, got {type(x).__name__}"
        raise TypeError(msg)
    return x


def from_datetime(x: str) -> datetime:
    """Parse and return a datetime value from a string.

    Args:
        x: ISO format datetime string to parse.

    Returns:
        Parsed datetime object.
    """
    return dateutil.parser.parse(x)


def to_class[T](c: type[T], x: T) -> dict:  # pragma: no cover
    """Convert an object to its dictionary representation.

    Args:
        c: The class type of the object.
        x: The object instance to convert.

    Returns:
        Dictionary representation of the object.

    Raises:
        TypeError: If x is not an instance of c.
    """
    if not isinstance(x, c):
        msg = f"Expected instance of {c.__name__}, got {type(x).__name__}"
        raise TypeError(msg)
    return x.to_dict()  # type: ignore[attr-defined]


@dataclass
class ActivityFeedItem:
    """Represents an activity feed item from Wealthsimple API.

    Attributes:
        account_id: The account identifier.
        occurred_at: The timestamp when the activity occurred.
        amount: The transaction amount.
        asset_symbol: The symbol of the asset involved.
        security_id: The security identifier.
        status: The status of the transaction.
        sub_type: The sub-type of the transaction.
        type: The type of transaction.
        typename: The GraphQL typename.
        description: Description of the activity.
    """

    account_id: str | None
    aft_originator_name: str | None
    aft_transaction_category: str | None
    aft_transaction_type: str | None
    amount: str | None
    amount_sign: str | None
    asset_quantity: str | None
    asset_symbol: str | None
    canonical_id: str | None
    currency: str | None
    e_transfer_email: str | None
    e_transfer_name: str | None
    external_canonical_id: str | None
    identity_id: str | None
    institution_name: str | None
    occurred_at: datetime
    p2_p_handle: str | None
    p2_p_message: str | None
    spend_merchant: str | None
    security_id: str | None
    bill_pay_company_name: str | None
    bill_pay_payee_nickname: str | None
    redacted_external_account_number: str | None
    opposing_account_id: str | None
    status: str | None
    sub_type: str | None
    type: str | None
    strike_price: str | None
    contract_type: str | None
    expiry_date: str | None
    cheque_number: str | None
    provisional_credit_amount: str | None
    primary_blocker: str | None
    interest_rate: str | None
    frequency: str | None
    counter_asset_symbol: str | None
    reward_program: str | None
    counter_party_currency: str | None
    counter_party_currency_amount: str | None
    counter_party_name: str | None
    fx_rate: str | None
    fees: str | None
    reference: str | None
    typename: str | None
    description: str | None

    @staticmethod
    def from_dict(obj: dict) -> "ActivityFeedItem":
        """Create ActivityFeedItem from dictionary.

        Args:
            obj: Dictionary containing activity feed item data.

        Returns:
            Parsed activity feed item instance.

        Raises:
            TypeError: If obj is not a dictionary.
        """
        if not isinstance(obj, dict):
            msg = f"Expected dict, got {type(obj).__name__}"
            raise TypeError(msg)

        account_id = from_str(obj.get("accountId"))
        aft_originator_name = from_str(obj.get("aftOriginatorName"))
        aft_transaction_category = from_str(
            obj.get("aftTransactionCategory"),
        )
        aft_transaction_type = from_str(obj.get("aftTransactionType"))
        amount = from_str(obj.get("amount"))
        amount_sign = from_str(obj.get("amountSign"))
        asset_quantity = from_str(obj.get("assetQuantity"))
        asset_symbol = from_str(obj.get("assetSymbol"))
        canonical_id = from_str(obj.get("canonicalId"))
        currency = from_str(obj.get("currency"))
        e_transfer_email = from_str(obj.get("eTransferEmail"))
        e_transfer_name = from_str(obj.get("eTransferName"))
        external_canonical_id = from_str(obj.get("externalCanonicalId"))
        identity_id = from_str(obj.get("identityId"))
        institution_name = from_str(obj.get("institutionName"))
        occurred_at = from_datetime(obj.get("occurredAt", ""))
        p2_p_handle = from_str(obj.get("p2pHandle"))
        p2_p_message = from_str(obj.get("p2pMessage"))
        spend_merchant = from_str(obj.get("spendMerchant"))
        security_id = from_str(obj.get("securityId"))
        bill_pay_company_name = from_str(obj.get("billPayCompanyName"))
        bill_pay_payee_nickname = from_str(obj.get("billPayPayeeNickname"))
        redacted_external_account_number = from_str(
            obj.get("redactedExternalAccountNumber"),
        )
        opposing_account_id = from_str(obj.get("opposingAccountId"))
        status = from_str(obj.get("status"))
        sub_type = from_str(obj.get("subType"))
        type_value = from_str(obj.get("type"))
        strike_price = from_str(obj.get("strikePrice"))
        contract_type = from_str(obj.get("contractType"))
        expiry_date = from_str(obj.get("expiryDate"))
        cheque_number = from_str(obj.get("chequeNumber"))
        provisional_credit_amount = from_str(
            obj.get("provisionalCreditAmount"),
        )
        primary_blocker = from_str(obj.get("primaryBlocker"))
        interest_rate = from_str(obj.get("interestRate"))
        frequency = from_str(obj.get("frequency"))
        counter_asset_symbol = from_str(obj.get("counterAssetSymbol"))
        reward_program = from_str(obj.get("rewardProgram"))
        counter_party_currency = from_str(
            obj.get("counterPartyCurrency"),
        )
        counter_party_currency_amount = from_str(
            obj.get("counterPartyCurrencyAmount"),
        )
        counter_party_name = from_str(obj.get("counterPartyName"))
        fx_rate = from_str(obj.get("fxRate"))
        fees = from_str(obj.get("fees"))
        reference = from_str(obj.get("reference"))
        typename = from_str(obj.get("__typename", ""))
        description = from_str(obj.get("description", ""))

        return ActivityFeedItem(
            account_id,
            aft_originator_name,
            aft_transaction_category,
            aft_transaction_type,
            amount,
            amount_sign,
            asset_quantity,
            asset_symbol,
            canonical_id,
            currency,
            e_transfer_email,
            e_transfer_name,
            external_canonical_id,
            identity_id,
            institution_name,
            occurred_at,
            p2_p_handle,
            p2_p_message,
            spend_merchant,
            security_id,
            bill_pay_company_name,
            bill_pay_payee_nickname,
            redacted_external_account_number,
            opposing_account_id,
            status,
            sub_type,
            type_value,
            strike_price,
            contract_type,
            expiry_date,
            cheque_number,
            provisional_credit_amount,
            primary_blocker,
            interest_rate,
            frequency,
            counter_asset_symbol,
            reward_program,
            counter_party_currency,
            counter_party_currency_amount,
            counter_party_name,
            fx_rate,
            fees,
            reference,
            typename,
            description,
        )

    def to_dict(self) -> dict:  # pragma: no cover
        """Convert ActivityFeedItem to dictionary representation.

        Returns:
            Dictionary representation of the activity feed item.
        """
        result: dict = {}
        result["accountId"] = from_str(self.account_id)
        result["aftOriginatorName"] = from_str(self.aft_originator_name)
        result["aftTransactionCategory"] = from_str(
            self.aft_transaction_category,
        )
        result["aftTransactionType"] = from_str(self.aft_transaction_type)
        result["amount"] = from_str(self.amount)
        result["amountSign"] = from_str(self.amount_sign)
        result["assetQuantity"] = from_str(self.asset_quantity)
        result["assetSymbol"] = from_str(self.asset_symbol)
        result["canonicalId"] = from_str(self.canonical_id)
        result["currency"] = from_str(self.currency)
        result["eTransferEmail"] = from_str(self.e_transfer_email)
        result["eTransferName"] = from_str(self.e_transfer_name)
        result["externalCanonicalId"] = from_str(self.external_canonical_id)
        result["identityId"] = from_str(self.identity_id)
        result["institutionName"] = from_str(self.institution_name)
        result["occurredAt"] = self.occurred_at.isoformat()
        result["p2pHandle"] = from_str(self.p2_p_handle)
        result["p2pMessage"] = from_str(self.p2_p_message)
        result["spendMerchant"] = from_str(self.spend_merchant)
        result["securityId"] = from_str(self.security_id)
        result["billPayCompanyName"] = from_str(self.bill_pay_company_name)
        result["billPayPayeeNickname"] = from_str(
            self.bill_pay_payee_nickname,
        )
        result["redactedExternalAccountNumber"] = from_str(
            self.redacted_external_account_number,
        )
        result["opposingAccountId"] = from_str(self.opposing_account_id)
        result["status"] = from_str(self.status)
        result["subType"] = from_str(self.sub_type)
        result["type"] = from_str(self.type)
        result["strikePrice"] = from_str(self.strike_price)
        result["contractType"] = from_str(self.contract_type)
        result["expiryDate"] = from_str(self.expiry_date)
        result["chequeNumber"] = from_str(self.cheque_number)
        result["provisionalCreditAmount"] = from_str(
            self.provisional_credit_amount,
        )
        result["primaryBlocker"] = from_str(self.primary_blocker)
        result["interestRate"] = from_str(self.interest_rate)
        result["frequency"] = from_str(self.frequency)
        result["counterAssetSymbol"] = from_str(self.counter_asset_symbol)
        result["rewardProgram"] = from_str(self.reward_program)
        result["counterPartyCurrency"] = from_str(
            self.counter_party_currency,
        )
        result["counterPartyCurrencyAmount"] = from_str(
            self.counter_party_currency_amount,
        )
        result["counterPartyName"] = from_str(self.counter_party_name)
        result["fxRate"] = from_str(self.fx_rate)
        result["fees"] = from_str(self.fees)
        result["reference"] = from_str(self.reference)
        result["__typename"] = from_str(self.typename)
        result["description"] = from_str(self.description)
        return result
