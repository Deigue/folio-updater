"""Activity Feed Item from Wealthsimple."""

from dataclasses import dataclass
from datetime import datetime

from models.base import SerializableModel, from_datetime, from_str


@dataclass
class ActivityFeedItem(SerializableModel):
    """Activity feed item from Wealthsimple API."""

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
        """Create ActivityFeedItem from a dictionary."""
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
