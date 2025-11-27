"""Wealthsimple Service.

This service handles interactions with the Wealthsimple API.

Authentication Tokens are managed using the keyring library.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import tempfile
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any

import keyring
import ws_api
from keyring.errors import PasswordDeleteError
from ws_api import (
    LoginFailedException,
    OTPRequiredException,
    WealthsimpleAPI,
    WSAPISession,
)

from app.app_context import get_config
from models.wealthsimple import (
    Account,
    ActivityFeedItem,
    BrokerageMonthlyStatementTransaction,
)
from utils.constants import TXN_ESSENTIALS, Action
from utils.transforms import normalize_canadian_ticker

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from utils.config import Config


logger = logging.getLogger(__name__)

KEYRING_SERVICE = "folio-updater.wealthsimple"
KEYRING_USERNAME_KEY = "default_username"


class WealthsimpleServiceError(Exception):
    """Base exception for Wealthsimple service errors."""


class WealthsimpleAuthenticationError(WealthsimpleServiceError):
    """Raised when authentication fails."""


class WealthsimpleAPIError(WealthsimpleServiceError):
    """Raised when API returns an error."""


class WealthsimpleTimeoutError(WealthsimpleServiceError):
    """Raised when requests timeout."""


class WealthsimpleDataError(WealthsimpleServiceError):
    """Raised when data parsing/validation fails."""


class WealthsimpleService:
    """Service for interacting with the Wealthsimple API."""

    def __init__(self, username: str | None = None) -> None:
        """Initialize the WealthsimpleService.

        Args:
            username: Wealthsimple username (email). If None, will use stored or prompt.
        """
        self._username: str | None = username
        self._ws_api: WealthsimpleAPI | None = None
        self._session: WSAPISession | None = None

        config: Config = get_config()
        ws_config = config.brokers.get("wealthsimple", {})
        user_agent: str = ws_config.get(
            "user_agent",
            (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) "
                "Gecko/20100101 Firefox/143.0"
            ),
        )
        WealthsimpleAPI.set_user_agent(user_agent)

    def _get_keyring_session_key(self, username: str) -> str:
        """Get the keyring key for storing session data."""
        return f"{KEYRING_SERVICE}.{username}"

    def _get_stored_username(self) -> str | None:
        """Get the stored default username from keyring."""
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME_KEY)

    def _store_username(self, username: str) -> None:
        """Store the username as the default in keyring."""
        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME_KEY, username)
        logger.debug("STORED username as default: %s", username)

    def _persist_session_callback(self) -> Callable[..., None]:
        """Get a callback function for persisting ws-api sessions."""

        def persist_callback(session_json: str, username: str) -> None:
            keyring.set_password(
                self._get_keyring_session_key(username),
                username,
                session_json,
            )
            self._store_username(username)

        return persist_callback

    def _load_session(self, username: str) -> WSAPISession | None:
        """Load session from keyring."""
        session_json = keyring.get_password(
            self._get_keyring_session_key(username),
            username,
        )
        if session_json:
            try:
                return WSAPISession.from_json(session_json)
            except json.JSONDecodeError as e:
                logger.warning(
                    "Failed to load session from keyring for %s: %s",
                    username,
                    e,
                )
                return None
        return None  # pragma: no cover

    def _setup_security_cache(self) -> None:  # pragma: no cover
        """Set up security market data cache using temp directory."""
        if not self._ws_api:
            return

        def sec_info_getter_fn(ws_security_id: str) -> dict[str, Any] | None:
            temp_dir = Path(tempfile.gettempdir())
            cache_file_path = temp_dir / f"ws-api-{ws_security_id}.json"
            if cache_file_path.exists():
                try:
                    with cache_file_path.open() as f:
                        return json.load(f)
                except (OSError, json.JSONDecodeError) as e:
                    logger.warning(
                        "Failed to load security cache for %s: %s",
                        ws_security_id,
                        e,
                    )
            return None

        def sec_info_setter_fn(
            ws_security_id: str,
            market_data: dict[str, Any],
        ) -> dict[str, Any]:
            temp_dir = Path(tempfile.gettempdir())
            cache_file_path = temp_dir / f"ws-api-{ws_security_id}.json"
            try:
                with cache_file_path.open("w") as f:
                    json.dump(market_data, f)
            except OSError as e:
                logger.warning(
                    "Failed to save security cache for %s: %s",
                    ws_security_id,
                    e,
                )
            return market_data

        self._ws_api.set_security_market_data_cache(
            sec_info_getter_fn,
            sec_info_setter_fn,
        )
        logger.debug("Security market data cache configured")

    def login(
        self,
        username: str | None = None,
        prompt_func: Callable[[str], str] | None = None,
        password_prompt_func: Callable[[str], str] | None = None,
    ) -> None:
        """Login to Wealthsimple.

        Args:
            username: Wealthsimple username (email)
            prompt_func: Function to use for prompting (input())
            password_prompt_func: Function for password prompting (input())

        Raises:
            WealthsimpleAuthenticationError: If login fails
        """
        if prompt_func is None:  # pragma: no cover
            prompt_func = input
        if password_prompt_func is None:  # pragma: no cover
            password_prompt_func = input

        if username:  # pragma: no cover
            self._username = username
        elif not self._username:
            self._username = self._get_stored_username()

        if not self._username:  # pragma: no cover
            self._username = prompt_func("Wealthsimple username (email): ")

        self._session = self._load_session(self._username)
        if self._session:
            try:
                self._ws_api = WealthsimpleAPI.from_token(
                    self._session,
                    self._persist_session_callback(),
                    self._username,
                )
            except ws_api.WSApiException as e:
                logger.warning(
                    "Existing session invalid for %s, will re-authenticate: %s",
                    self._username,
                    e,
                )
                self._session = None
            else:
                logger.debug("Using existing session for user: %s", self._username)
                self._setup_security_cache()
                return

        self._interactive_login(prompt_func, password_prompt_func)  # pragma: no cover
        self._setup_security_cache()  # pragma: no cover

    def _interactive_login(
        self,
        prompt_func: Callable[[str], str],
        password_prompt_func: Callable[[str], str],
    ) -> None:  # pragma: no cover
        """Perform interactive login to Wealthsimple."""
        username: str | None = self._username
        password = None
        otp_answer = None

        while True:
            try:
                if not username:
                    username = prompt_func("Wealthsimple username (email): ")
                    self._username = username
                if not password:
                    password = password_prompt_func("Password: ")
                WealthsimpleAPI.login(
                    username,
                    password,
                    otp_answer,
                    persist_session_fct=self._persist_session_callback(),
                )

                self._session = self._load_session(username)
                if not self._session:
                    msg = "Failed to load session after login"
                    raise WealthsimpleAuthenticationError(msg)

                self._ws_api = WealthsimpleAPI.from_token(
                    self._session,
                    self._persist_session_callback(),
                    username,
                )
                logger.info("SUCCESS: Logged in to Wealthsimple.")
                break
            except OTPRequiredException:
                otp_answer = prompt_func("TOTP code: ")
            except LoginFailedException:
                logger.exception("LOGIN FAILED for user %s", username)
                username = None
                password = None
                otp_answer = None

    def reset_credentials(
        self,
        username: str | None = None,
    ) -> None:  # pragma: no cover
        """Reset stored credentials and force re-authentication.

        Args:
            username: Specific username to reset. If None, uses stored username.

        This will clear the stored username and session data, forcing
        the user to re-enter credentials on next authentication.
        """
        if username is None:
            username = self._get_stored_username()
        if username:
            try:
                keyring.delete_password(
                    self._get_keyring_session_key(username),
                    username,
                )
            except PasswordDeleteError:
                logger.debug(
                    "No session data found to clear for user: %s",
                    username,
                )

        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME_KEY)
            logger.debug("Cleared stored default username")
        except PasswordDeleteError:
            logger.debug("No default username found to clear")

        self._username = None
        self._session = None
        self._ws_api = None
        logger.info("RESET credentials - will prompt again on next authentication.")

    def ensure_authenticated(self) -> WealthsimpleAPI:
        """Ensure we have an authenticated API instance.

        Returns:
            WealthsimpleAPI: Authenticated API instance

        Raises:
            WealthsimpleAuthenticationError: If not authenticated
        """
        if not self._ws_api:
            self.login()
            if not self._ws_api:
                msg = "Login failed to authenticate"
                raise WealthsimpleAuthenticationError(msg)
        return self._ws_api

    def get_accounts(self) -> list[Account]:
        """Get all Wealthsimple accounts.

        Returns:
            List of Account objects

        Raises:
            WealthsimpleServiceError: If API not initialized
        """
        ws = self.ensure_authenticated()
        accounts = ws.get_accounts()
        accounts = [Account.from_dict(a) for a in accounts]
        config = get_config()
        ws_config = config.brokers.get("wealthsimple", {})
        if ws_config.get("exclude_accounts"):
            excluded = ws_config["exclude_accounts"]
            accounts = [
                a
                for a in accounts
                if a.description is not None and a.description not in excluded
            ]
            logger.debug("EXCLUDED accounts %s", excluded)

        logger.debug("RETRIEVED %d accounts", len(accounts))
        return accounts

    def get_activities(
        self,
        account_id: str | list[str],
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        *,
        load_all: bool = False,
    ) -> list[ActivityFeedItem]:
        """Get activities (transactions) for an account.

        Args:
            account_id: Account ID to retrieve activities for
            start_date: Optional start date filter
            end_date: Optional end date filter
            load_all: Whether to load all pages of results

        Returns:
            List of activity feed items

        Raises:
            WealthsimpleServiceError: If API not initialized
        """
        ws = self.ensure_authenticated()
        activities = ws.get_activities(
            account_id,
            start_date=start_date,
            end_date=end_date,
            load_all=load_all,
        )
        logger.info(
            "RETRIEVED %d activities for accounts: %s",
            len(activities) if activities else 0,
            account_id,
        )
        return [ActivityFeedItem.from_dict(activity) for activity in (activities or [])]

    def get_monthly_statement(
        self,
        account_id: str,
        period: str,
    ) -> list[BrokerageMonthlyStatementTransaction]:
        """Get monthly statement for a given period.

        Args:
            account_id: The ID of the account to retrieve the statement for
            period: Date in 'YYYY-MM-DD' format
                Example: '2024-05-01' for May 2024 statement.

        Returns:
            List of BrokerageMonthlyStatementTransaction entries

        Raises:
            WealthsimpleServiceError: If API not initialized
        """
        ws = self.ensure_authenticated()
        statement = ws.get_statement_transactions(account_id, period)
        logger.info("RETRIEVED monthly statement for period: %s", period)
        return [
            BrokerageMonthlyStatementTransaction.from_dict(txn)
            for txn in (statement or [])
        ]

    def get_account_balances(
        self,
        account_id: str,
    ) -> dict[str, float]:  # pragma: no cover
        """Get balances for a specific account.

        Args:
            account_id: Account ID to retrieve balances for

        Returns:
            Dictionary mapping security IDs to quantities

        Raises:
            WealthsimpleServiceError: If API not initialized
        """
        ws = self.ensure_authenticated()
        balances = ws.get_account_balances(account_id)
        logger.debug("Retrieved balances for account: %s", account_id)
        return balances

    def export_activities_to_csv(
        self,
        activities: list[ActivityFeedItem],
        csv_name: str,
    ) -> None:
        """Export activity feed items to a CSV file.

        Args:
            activities: List of activity feed items to export
            csv_name: Name of the output CSV file
        """
        config = get_config()
        output_path: Path = config.imports_path / csv_name
        rows = [self._convert_activity_to_csv_row(activity) for activity in activities]

        with output_path.open("w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(TXN_ESSENTIALS)
            writer.writerows(rows)

        logger.info('EXPORTED %d activities to CSV: "%s"', len(activities), output_path)

    def export_statement_to_csv(
        self,
        statement_txns: list[BrokerageMonthlyStatementTransaction],
        csv_name: str,
    ) -> None:
        """Export statement transactions to a CSV file.

        Args:
            statement_txns: List of statement transactions to export
            csv_name: Name of the output CSV file
        """
        config = get_config()
        output_path: Path = config.statements_path / csv_name
        headers = ["date", "amount", "currency", "transaction", "description"]
        rows = [self._convert_statement_txn_to_csv_row(txn) for txn in statement_txns]

        with output_path.open("w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(headers)
            writer.writerows(rows)

        logger.info(
            'EXPORTED %d statement transactions to CSV: "%s"',
            len(statement_txns),
            output_path,
        )

    def _convert_activity_to_csv_row(self, activity: ActivityFeedItem) -> list[str]:
        """Convert an ActivityFeedItem to a CSV row.

        Args:
            activity: The activity feed item to convert

        Returns:
            List of string values for CSV row
        """
        action = self._normalize_action(
            activity.type,
            activity.sub_type,
            activity.amount,
        )
        ticker = normalize_canadian_ticker(activity.asset_symbol, activity.currency)
        account = self._map_account_id(activity.account_id)
        amount = self._normalize_amount(activity.amount, action, activity.amount_sign)
        units = self._normalize_units(activity.asset_quantity)
        price = self._get_price_string(activity.amount, activity.asset_quantity)

        # Handle stock splits: extract ratio from description and override price/units
        if action == Action.SPLIT and activity.description:
            split_from, split_to = self._extract_split_ratio(activity.description)
            if split_from and split_to:
                price = str(split_from)
                units = str(split_to)

        return [
            activity.occurred_at.strftime("%Y-%m-%d"),  # TxnDate
            action,  # Action
            amount,  # Amount
            activity.currency or "",  # $
            price,  # Price
            units,  # Units
            ticker or "",  # Ticker
            account,  # Account
        ]

    def _convert_statement_txn_to_csv_row(
        self,
        statement_txn: BrokerageMonthlyStatementTransaction,
    ) -> list[str]:
        """Convert a BrokerageMonthlyStatementTransaction to a statement CSV row.

        Expected format: ["date", "amount", "currency", "transaction", "description"]

        Args:
            statement_txn: The statement transaction to convert

        Returns:
            List of string values for statement CSV row
        """
        date_str = statement_txn.transaction_date.strftime("%Y-%m-%d")
        amount = statement_txn.cash_movement or "0"
        # Extract currency code from unit field (e.g., "$CAD" -> "CAD")
        currency = "CAD"
        if statement_txn.unit:
            currency = statement_txn.unit.lstrip("$").upper()

        transaction_type = statement_txn.transaction_type or ""
        description = statement_txn.description or ""

        return [
            date_str,  # date
            amount,  # amount
            currency,  # currency
            transaction_type,  # transaction
            description,  # description
        ]

    @staticmethod
    def _normalize_action(
        action_type: str | None,
        sub_type: str | None,
        amount: str | None,
    ) -> str:
        """Normalize action type to standard actions.

        Args:
            action_type: The action type from activity
            sub_type: The sub-type from activity
            amount: The transaction amount

        Returns:
            Normalized action string
        """
        action = action_type or ""
        if action == "DIY_BUY":
            return Action.BUY
        if action == "DIY_SELL":
            return Action.SELL
        if action == "CORPORATE_ACTION" and sub_type == "SUBDIVISION":
            return Action.SPLIT
        if action in {"INTERNAL_TRANSFER", "INSTITUTIONAL_TRANSFER_INTENT"}:
            if sub_type in {"SOURCE", "TRANSFER_OUT"}:
                # Institutional transfers may have null amounts. This will needs to be
                # entered manually by the user later.
                if amount is None:
                    amount = "0"
                return Action.WITHDRAWAL
            return Action.CONTRIBUTION
        return action  # pragma: no cover

    @staticmethod
    def _normalize_amount(
        amount: str | None,
        action: str,
        amount_sign: str | None,
    ) -> str:
        """Normalize amount, making it negative for BUY actions.

        Args:
            amount: The transaction amount
            action: The normalized action type
            amount_sign: The sign indicator from activity

        Returns:
            Normalized amount string
        """
        normalized = amount or ""
        sign = amount_sign or ""
        if action == Action.BUY:
            sign = "negative"

        if normalized:
            try:
                amount_val = Decimal(str(normalized))
                if amount_val > 0 and sign == "negative":
                    normalized = str(-amount_val)
            except (ValueError, TypeError):
                pass

        return normalized

    @staticmethod
    def _normalize_units(units: str | None) -> str:
        """Normalize units, removing unnecessary trailing zeros.

        Args:
            units: The asset quantity

        Returns:
            Normalized units string
        """
        normalized = units or ""
        if normalized:
            try:
                units_val = Decimal(str(normalized))
                normalized = str(units_val.normalize())
            except (ValueError, TypeError):
                pass

        return normalized

    @staticmethod
    def _extract_split_ratio(description: str) -> tuple[Decimal | None, Decimal | None]:
        """Extract stock split ratio from description.

        Parses description like "Subdivision: 60.5 -> 90.75 shares of XYZ"
        to extract the held shares (from) and total shares (to).

        For example: if held_shares=60 and total_shares=90,
        this represents a split where for every 2 old shares, you get 3 new shares.
        The function returns (2, 3) representing the FROM:TO ratio.

        Args:
            description: The activity description containing split information

        Returns:
            Tuple of (split_from_ratio, split_to_ratio) as Decimals,
            or (None, None) if parsing fails.
        """
        if not description:  # pragma: no cover
            return None, None

        # Match pattern: "Subdivision: X -> Y shares" where X and Y can be floats
        pattern = r"(\d+(?:\.\d+)?)\s*->\s*(\d+(?:\.\d+)?)\s*shares"
        match = re.search(pattern, description)
        if not match:  # pragma: no cover
            return None, None

        try:
            held_shares = float(match.group(1))
            total_shares = float(match.group(2))

            if held_shares <= 0 or total_shares <= 0:  # pragma: no cover
                return None, None

            # Use fractions to calculate exact ratio and avoid floating point errors
            # Calculate the ratio as total_shares / held_shares (new per old)
            held_fraction = Fraction(held_shares).limit_denominator()
            total_fraction = Fraction(total_shares).limit_denominator()
            ratio = total_fraction / held_fraction

            # Convert to simplest integer ratio (FROM : TO)
            split_from = Decimal(ratio.denominator)
            split_to = Decimal(ratio.numerator)
        except (ValueError, ZeroDivisionError) as e:
            logger.warning("Failed to extract split ratio from description: %s", e)
            return None, None
        else:
            return split_from, split_to

    def _get_price_string(self, amount: str | None, units: str | None) -> str:
        """Get price string formatted for CSV output.

        Args:
            amount: The transaction amount
            units: The asset quantity

        Returns:
            Price string, empty if not calculable
        """
        if not amount or not units:
            return ""

        price_val: Decimal = self._calculate_price(amount, units)
        return str(price_val) if price_val > 0 else ""

    @staticmethod
    def _map_account_id(account_id: str | None) -> str:
        """Map account_id to account name.

        Args:
            account_id: The account identifier

        Returns:
            Mapped account name
        """
        account: str = "UNKNOWN"
        if not account_id:  # pragma: no cover
            return account
        account_lower = account_id.lower()
        if "tfsa" in account_lower:
            account = "WS-TFSA"
        if "non-registered" in account_lower:
            account = "WS-PERSONAL"
        return account

    @staticmethod
    def _calculate_price(amount: str | None, units: str | None) -> Decimal:
        """Calculate price per unit from amount and units.

        Args:
            amount: The transaction amount
            units: The number of units

        Returns:
            Price per unit
        """
        if not amount or not units:  # pragma: no cover
            return Decimal(0)
        try:
            amount_val = Decimal(str(amount)).copy_abs()
            units_val = Decimal(str(units)).copy_abs()
            if units_val == 0:  # pragma: no cover
                return Decimal(0)
            price = amount_val / units_val
            return price.quantize(Decimal("0.01"))
        except (ValueError, TypeError, ZeroDivisionError):
            return Decimal(0)
