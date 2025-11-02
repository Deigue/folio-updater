"""Wealthsimple Service.

This service handles interactions with the Wealthsimple API.

Authentication Tokens are managed using the keyring library.
"""

from __future__ import annotations

import json
import logging
import tempfile
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
        logger.debug("Stored username as default: %s", username)

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
        return None

    def _setup_security_cache(self) -> None:
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

        if not self._username:
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

        self._interactive_login(prompt_func, password_prompt_func)
        self._setup_security_cache()

    def _interactive_login(
        self,
        prompt_func: Callable[[str], str],
        password_prompt_func: Callable[[str], str],
    ) -> None:
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
                logger.info("Successfully logged in to Wealthsimple.")
                break
            except OTPRequiredException:
                otp_answer = prompt_func("TOTP code: ")
            except LoginFailedException:
                logger.exception("Login failed for user %s", username)
                username = None
                password = None
                otp_answer = None

    def reset_credentials(self, username: str | None = None) -> None:
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

    def get_accounts(self) -> list[dict[str, Any]]:
        """Get all Wealthsimple accounts.

        Returns:
            List of account dictionaries

        Raises:
            WealthsimpleServiceError: If API not initialized
        """
        ws = self.ensure_authenticated()
        accounts = ws.get_accounts()
        logger.info("Retrieved %d accounts", len(accounts))
        return accounts

    def get_activities(
        self,
        account_id: str | list[str],
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        *,
        load_all: bool = False,
    ) -> list[dict[str, Any]]:
        """Get activities (transactions) for an account.

        Args:
            account_id: Account ID to retrieve activities for
            start_date: Optional start date filter
            end_date: Optional end date filter
            load_all: Whether to load all pages of results

        Returns:
            List of activity dictionaries

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
            "Retrieved %d activities for account: %s",
            len(activities) if activities else 0,
            account_id,
        )
        return activities or []

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
