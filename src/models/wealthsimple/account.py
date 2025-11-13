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
class Account:
    """Wealthsimple account data.

    Attributes:
        id (str): The unique identifier for the account.

    """

    id: str
