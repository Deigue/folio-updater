"""Wealthsimple Service.

This service handles interactions with the Wealthsimple API.

Authentication Tokens are managed using the keyring library.
"""

KEYRING_SERVICE = "folio-updater.wealthsimple"


class WealthsimpleService:
    """Service for interacting with the Wealthsimple API."""

    def __init__(self) -> None:
        """Initialize the WealthsimpleService."""
