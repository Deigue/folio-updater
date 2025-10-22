"""Interactive Brokers Service.

This module provides functions to interact with the Interactive Brokers API..

Authentication is handled using keyring for secure token storage.
"""

# ruff: noqa: S314
from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple, Self
from urllib.parse import urlencode

import keyring
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.app_context import get_config
from utils.constants import TORONTO_TZ

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType


logger = logging.getLogger(__name__)

IBKR_SENDREQUEST_URL = (
    "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
)
IBKR_GETSTATEMENT_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement"
KEYRING_SYSTEM = "ibkr"
KEYRING_USERNAME = "flex_token"
MAX_RETRIES = 3
RETRY_BACKOFF_FACTOR = 1
RETRY_STATUS_FORCELIST = [429, 500, 502, 503, 504]
INITIAL_WAIT_SECONDS = 10
POLL_INTERVAL_SECONDS = 30
MAX_POLL_ATTEMPTS = 10


class IBKRServiceError(Exception):
    """Base exception for IBKR service errors."""


class IBKRAuthenticationError(IBKRServiceError):
    """Raised when authentication fails."""


class IBKRAPIError(IBKRServiceError):
    """Raised when API returns an error."""


class IBKRTimeoutError(IBKRServiceError):
    """Raised when statement generation times out."""


class DownloadRequest(NamedTuple):
    """Parameters for downloading a statement."""

    query_name: str
    query_id: str
    from_date: str
    to_date: str
    version: str = "3"


class IBKRService:
    """Service for interacting with Interactive Brokers."""

    def __init__(self) -> None:
        """Initialize the IBKR service with retry configuration."""
        self._session: requests.Session

    def __enter__(self) -> Self:
        """Enter context manager - initialize session."""
        self._session = requests.Session()
        retry_strategy = Retry(
            total=MAX_RETRIES,
            backoff_factor=RETRY_BACKOFF_FACTOR,
            status_forcelist=RETRY_STATUS_FORCELIST,
            allowed_methods=["GET", "POST"],
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit context manager - close session."""
        self._session.close()

    def _parse_xml_error(
        self,
        xml_content: str,
        *,
        for_statement: bool = False,
    ) -> bool:
        """Parse XML response and raise appropriate exception if error found.

        Args:
            xml_content: XML response content from IBKR API
            for_statement: If True, handle "not ready" codes for statement retrieval

        Raises:
            IBKRAPIError: If the XML contains an error response

        Returns:
            bool: True if no error found, False if statement not ready
        """
        try:
            root = ET.fromstring(xml_content)
            status_elem = root.find("Status")

            if status_elem is not None and status_elem.text == "Fail":
                error_code_elem = root.find("ErrorCode")
                error_msg_elem = root.find("ErrorMessage")

                if error_code_elem is not None and error_msg_elem is not None:
                    error_code = error_code_elem.text
                    error_message = error_msg_elem.text

                    # Special handling for "statement not ready" codes
                    if for_statement and error_code in ("1003", "1019"):
                        logger.debug(
                            "Statement not ready yet (code %s): %s",
                            error_code,
                            error_message,
                        )
                        return False

                    msg = f"IBKR API Error {error_code}: {error_message}"
                    logger.error(msg)
                    raise IBKRAPIError(msg)

        except ET.ParseError as e:
            msg = f"Failed to parse XML response: {e}"
            logger.exception(msg)
            raise IBKRAPIError(msg) from e

        return True  # No error found

    def get_token(self) -> str:
        """Retrieve the flex token from keyring.

        Returns:
            str: The flex token

        Raises:
            IBKRAuthenticationError: If no token is found in keyring
        """
        token = keyring.get_password(KEYRING_SYSTEM, KEYRING_USERNAME)
        if not token:
            msg = (
                "No flex token found in keyring. Please store your token using: "
                "folio --broker ibkr --token YOUR_TOKEN"
            )
            logger.error(msg)
            raise IBKRAuthenticationError(msg)
        return token

    def set_token(self, token: str) -> None:
        """Store the flex token in keyring.

        Args:
            token: The flex token to store
        """
        keyring.set_password(KEYRING_SYSTEM, KEYRING_USERNAME, token)
        logger.info("Flex token stored in keyring successfully")

    def send_request(
        self,
        request: DownloadRequest,
    ) -> str:
        """Send a request to generate a Flex statement.

        Args:
            request: DownloadRequest containing all parameters

        Returns:
            str: Reference code for retrieving the statement

        Raises:
            IBKRAuthenticationError: If authentication fails
            IBKRAPIError: If the API returns an error
        """
        token = self.get_token()
        from_date = request.from_date
        to_date = request.to_date

        if "Activity" in request.query_name:
            try:
                to_date_obj = datetime.strptime(to_date, "%Y%m%d").replace(
                    tzinfo=TORONTO_TZ,
                )
                today = datetime.now(TORONTO_TZ).date()

                if to_date_obj.date() >= today:
                    yesterday = today - timedelta(days=1)
                    to_date = yesterday.strftime("%Y%m%d")
                    msg = (
                        f"ADJUSTED: to_date {request.to_date} -> {to_date} for "
                        f"{request.query_name}"
                    )
                    logger.info(msg)
            except ValueError as e:
                msg = f"Failed to parse to_date {to_date}: {e}"
                logger.exception(msg)
                raise IBKRServiceError(msg) from e

        params: dict[str, str] = {
            "t": token,
            "q": request.query_id,
            "fd": from_date,
            "td": to_date,
            "v": request.version,
        }

        url: str = f"{IBKR_SENDREQUEST_URL}?{urlencode(params)}"

        try:
            logger.info(
                "REQUEST IBKR for %s from %s to %s",
                request.query_name,
                from_date,
                to_date,
            )
            response = self._session.get(url, timeout=30)
            response.raise_for_status()

        except requests.RequestException as e:
            msg = f"Failed to send request: {e}"
            logger.exception(msg)
            raise IBKRAPIError(msg) from e

        # Parse XML response
        try:
            logger.debug("Send request response: %s", response.text)
            self._parse_xml_error(response.text)
            root = ET.fromstring(response.text)
            reference_code_elem = root.find("ReferenceCode")
            if reference_code_elem is None or not reference_code_elem.text:
                msg = f"No reference code found in response: {response.text}"
                logger.error(msg)
                raise IBKRAPIError(msg)

        except ET.ParseError as e:
            msg = f"Failed to parse XML response: {e}"
            logger.exception(msg)
            raise IBKRAPIError(msg) from e

        reference_code = reference_code_elem.text
        logger.info("RECEIVED reference code: %s", reference_code)
        return reference_code

    def get_statement(self, reference_code: str) -> str:
        """Retrieve a Flex statement using the reference code.

        Args:
            reference_code: Reference code from send_request

        Returns:
            str: CSV content of the statement (empty if not ready)

        Raises:
            IBKRAPIError: If the API returns an error
            IBKRTimeoutError: If statement is not ready after polling
        """
        token = self.get_token()
        params = {
            "t": token,
            "q": reference_code,
            "v": "3",
        }
        url = f"{IBKR_GETSTATEMENT_URL}?{urlencode(params)}"

        try:
            logger.info("Retrieving statement for reference code: %s", reference_code)
            response = self._session.get(url, timeout=30)
            response.raise_for_status()

        except requests.RequestException as e:
            msg = f"Failed to get statement: {e}"
            logger.exception(msg)
            raise IBKRAPIError(msg) from e

        # Check whether XML or CSV response
        content = response.text.strip()
        if content.startswith("<"):
            is_ready = self._parse_xml_error(content, for_statement=True)
            if not is_ready:
                return ""

            msg = f"Unexpected XML response format: {content}"
            logger.error(msg)
            raise IBKRAPIError(msg)

        logger.info(
            "SUCCESS: Statement retrieved for reference code: %s",
            reference_code,
        )
        return content

    def download_statement_with_polling(
        self,
        request: DownloadRequest | str,
    ) -> str:
        """Download a Flex statement.

        Args:
            request: DownloadRequest with request parameters

        Returns:
            str: CSV content of the statement

        Raises:
            IBKRTimeoutError: If statement is not ready after max attempts
        """
        if isinstance(request, str):
            reference_code = request
        else:
            reference_code = self.send_request(request)
            logger.info(
                "WAIT %d seconds to allow statement to process...",
                INITIAL_WAIT_SECONDS,
            )
            time.sleep(INITIAL_WAIT_SECONDS)

        for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
            statement_data = self.get_statement(reference_code)
            if statement_data:
                return statement_data

            if attempt < MAX_POLL_ATTEMPTS:
                logger.info(
                    "POLL (%d/%d): Statement not ready, waiting %d seconds...",
                    attempt,
                    MAX_POLL_ATTEMPTS,
                    POLL_INTERVAL_SECONDS,
                )
                time.sleep(POLL_INTERVAL_SECONDS)

        msg = (
            f"Statement not ready after {MAX_POLL_ATTEMPTS} attempts. "
            f"You can retry later with reference code: {reference_code}"
        )
        logger.error(msg)
        raise IBKRTimeoutError(msg)

    def save_statement_as_csv(
        self,
        csv_content: str,
        request: DownloadRequest | str,
    ) -> int:
        """Save CSV statement content directly to file.

        Args:
            csv_content: CSV content from get_statement
            request: DownloadRequest containing all parameters

        Returns:
            int: Number of lines saved (including header)
        """
        if not csv_content:
            logger.warning("No CSV content to save")
            return 0

        config = get_config()
        if isinstance(request, str):
            csv_name = f"ibkr_ref_{request}.csv"
        else:
            csv_name = (
                f"ibkr_{request.query_name}_{request.from_date}_{request.to_date}.csv"
            )
        output_path: Path = config.imports_path / csv_name

        try:
            with output_path.open("w", encoding="utf-8") as f:
                f.write(csv_content)
        except OSError as e:
            msg = f"Failed to save CSV to {output_path}: {e}"
            logger.exception(msg)
            raise IBKRServiceError(msg) from e

        line_count = len(csv_content.strip().split("\n"))
        logger.info('Saved %d lines to "%s"', line_count, output_path)
        return line_count

    def download_and_save_statement(self, request: DownloadRequest | str) -> int:
        """Download a Flex query statement and save it as CSV.

        Args:
            request: DownloadRequest containing all parameters

        Returns:
            int: Total numer of lines saved
        """
        csv_content = self.download_statement_with_polling(request)
        return self.save_statement_as_csv(csv_content, request)
