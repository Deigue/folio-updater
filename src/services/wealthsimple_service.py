"""Wealthsimple Service.

This service handles interactions with the Wealthsimple API.

Authentication Tokens are managed using the keyring library.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ws_api import (
    LoginFailedException,
    OTPRequiredException,
    WealthsimpleAPI,
    WSAPISession,
)

from app.app_context import get_config

if TYPE_CHECKING:
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

