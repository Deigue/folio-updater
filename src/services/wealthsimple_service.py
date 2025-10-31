"""Wealthsimple Service.

This service handles interactions with the Wealthsimple API.

Authentication Tokens are managed using the keyring library.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import keyring
from ws_api import (
    LoginFailedException,
    OTPRequiredException,
    WealthsimpleAPI,
    WSAPISession,
)

from app.app_context import get_config

if TYPE_CHECKING:
    from collections.abc import Callable
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

