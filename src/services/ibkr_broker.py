"""Interactive Brokers, downloaded through its Flex Web Service.

The flex token is kept in the system keyring.
"""

# ruff: noqa: S314
from __future__ import annotations

import csv
import logging
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import keyring
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app import get_config
from domain import TORONTO_TZ, BrokerId
from services.broker import (
    PREVIEW_ROWS,
    BrokerError,
    BrokerFailure,
    DownloadResult,
    PartFailure,
    PreviewTable,
    resolve_window,
)
from services.broker_files import ibkr_file_name, ibkr_retry_file_name
from term import (
    announce,
    console_error,
    console_info,
    console_success,
    console_warning,
    prompt_secret,
)

if TYPE_CHECKING:
    from pathlib import Path

    from config.brokers import IbkrSettings
    from services.broker import Progress

logger = logging.getLogger(__name__)

IBKR_SENDREQUEST_URL = (
    "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
)
IBKR_GETSTATEMENT_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement"
KEYRING_SERVICE = "folio-updater.ibkr"
KEYRING_USERNAME = "flex_token"
MAX_RETRIES = 3
RETRY_BACKOFF_FACTOR = 1
RETRY_STATUS_FORCELIST = [429, 500, 502, 503, 504]
INITIAL_WAIT_SECONDS = 5
POLL_INTERVAL_SECONDS = 30
MAX_POLL_ATTEMPTS = 10
FLEX_VERSION = "3"

# Query IDs the default config.yaml ships with, standing for "not set up yet".
_PLACEHOLDER_QUERY_IDS = frozenset({"111111", "999999"})

# What GetStatement answers while the statement is still being generated.
_NOT_READY_CODES = frozenset({"1003", "1019"})

# The kind of failure each Flex Web Service error code means; any other is unexpected.
_FAILURES_BY_CODE = {
    "1012": BrokerFailure.CREDENTIALS_EXPIRED,
    **dict.fromkeys(("1011", "1013", "1015", "1016"), BrokerFailure.LOGIN_FAILED),
    **dict.fromkeys(("1010", "1014", "1020"), BrokerFailure.NOT_CONFIGURED),
    **dict.fromkeys(
        ("1001", "1004", "1005", "1006", "1007", "1008", "1009", "1018", "1021"),
        BrokerFailure.BUSY,
    ),
}


class IbkrBroker:
    """IBKR, one flex query statement per configured query."""

    id = BrokerId.IBKR

    def __init__(self, settings: IbkrSettings) -> None:
        """Download the queries `settings` names."""
        self._settings = settings

    # -- CREDENTIALS -------------------------------------------------------------

    def sign_in(self) -> None:
        """Ask for the flex token when the keyring holds none."""
        if keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME):
            return
        console_info("No IBKR flex token found in keyring.")
        self._store_token()

    def reset_credentials(self) -> None:
        """Replace the stored flex token with a new one."""
        console_info("Setting new IBKR flex token...")
        self._store_token()

    def _store_token(self) -> None:
        keyring.set_password(
            KEYRING_SERVICE,
            KEYRING_USERNAME,
            prompt_secret("Enter your IBKR flex token"),
        )
        announce.success("Flex token stored securely")

    def _token(self) -> str:
        token = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        if not token:
            raise BrokerError(
                self.id,
                BrokerFailure.LOGIN_FAILED,
                "No flex token found in keyring.",
            )
        return token

    # -- DOWNLOAD ----------------------------------------------------------------

    def download(
        self,
        start: date | None,
        end: date | None,
        progress: Progress,
    ) -> DownloadResult:
        """Download every configured query into the imports inbox.

        A query that fails is reported and the rest carry on. A query whose ID is
        still a placeholder is skipped.

        Raises:
            BrokerError: When no query is configured at all.
        """
        if not self._settings.queries:
            raise BrokerError(
                self.id,
                BrokerFailure.NOT_CONFIGURED,
                "No IBKR flex queries configured in config.yaml, under brokers.ibkr",
            )
        window = resolve_window(self.id, start, end)
        files: list[Path] = []
        failures: list[PartFailure] = []
        previews: list[PreviewTable] = []
        with _session() as session:
            for query_name, query_id in self._settings.queries.items():
                progress(f"Downloading {query_name}...")
                if not query_id or query_id in _PLACEHOLDER_QUERY_IDS:
                    console_warning(
                        f"Skipping {query_name}: No valid query ID configured",
                    )
                    continue
                try:
                    reference_code = self._send_request(
                        session,
                        query_name,
                        query_id,
                        window.start,
                        _flex_end(query_name, window.end),
                    )
                    msg = (
                        f"Waiting {INITIAL_WAIT_SECONDS} seconds to allow "
                        "statement to process..."
                    )
                    progress(msg)
                    time.sleep(INITIAL_WAIT_SECONDS)
                    content = self._poll(session, reference_code, progress)
                    name = ibkr_file_name(
                        query_name,
                        _flex_date(window.start),
                        _flex_date(window.end),
                    )
                    path, lines = self._save(content, name)
                except BrokerError as e:
                    logger.error("%s: %s", query_name, e)  # noqa: TRY400
                    console_error(f"{query_name}: {e}")
                    failures.append(PartFailure(query_name, e))
                    continue
                files.append(path)
                previews.append(_preview(query_name, content))
                console_success(f"{query_name}: {lines} lines received")
        return DownloadResult(
            files=tuple(files),
            failures=tuple(failures),
            previews=tuple(previews),
        )

    def retry(self, reference_code: str, progress: Progress) -> DownloadResult:
        """Fetch the statement of an earlier request by its reference code.

        Raises:
            BrokerError: When the statement cannot be fetched or saved.
        """
        with _session() as session:
            content = self._poll(session, reference_code, progress)
        path, lines = self._save(content, ibkr_retry_file_name(reference_code))
        console_success(f"{reference_code}: Received {lines} lines")
        return DownloadResult(
            files=(path,),
            previews=(_preview(reference_code, content),),
        )

    # -- FLEX WEB SERVICE ----------------------------------------------------------

    def _send_request(
        self,
        session: requests.Session,
        query_name: str,
        query_id: str,
        start: date,
        end: date,
    ) -> str:
        """Ask IBKR to generate a flex query statement, returning its reference code."""
        params = {
            "t": self._token(),
            "q": query_id,
            "fd": _flex_date(start),
            "td": _flex_date(end),
            "v": FLEX_VERSION,
        }
        announce.info(
            f"REQUEST IBKR for {query_name} from {params['fd']} to {params['td']}",
        )
        text = self._get(session, IBKR_SENDREQUEST_URL, params)
        root = self._parse(text)
        self._raise_on_failure(root)
        reference_code = root.findtext("ReferenceCode")
        if not reference_code:  # pragma: no cover
            msg = f"No reference code found in response: {text}"
            raise BrokerError(self.id, BrokerFailure.UNEXPECTED_RESPONSE, msg)
        logger.info("RECEIVED reference code: %s", reference_code)
        return reference_code

    def _poll(
        self,
        session: requests.Session,
        reference_code: str,
        progress: Progress,
    ) -> str:
        """Ask for a statement until it is ready, returning its CSV content."""
        for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
            progress("Downloading statement...")
            content = self._statement(session, reference_code)
            if content:
                return content
            if attempt < MAX_POLL_ATTEMPTS:
                msg = (
                    f"POLL ({attempt}/{MAX_POLL_ATTEMPTS}): Statement not ready, "
                    f"waiting {POLL_INTERVAL_SECONDS} seconds..."
                )
                progress(msg)
                logger.info(msg)
                time.sleep(POLL_INTERVAL_SECONDS)
        msg = (
            f"Statement not ready after {MAX_POLL_ATTEMPTS} attempts. "
            f"You can retry later with reference code: {reference_code}"
        )
        raise BrokerError(self.id, BrokerFailure.BUSY, msg)

    def _statement(self, session: requests.Session, reference_code: str) -> str:
        """Fetch a statement's CSV content, or an empty string while it is not ready."""
        params = {"t": self._token(), "q": reference_code, "v": FLEX_VERSION}
        logger.info("Retrieving statement for reference code: %s", reference_code)
        content = self._get(session, IBKR_GETSTATEMENT_URL, params).strip()
        if not content.startswith("<"):
            logger.info(
                "SUCCESS: Statement retrieved for reference code: %s",
                reference_code,
            )
            return content
        root = self._parse(content)
        self._raise_on_failure(root, unless=_NOT_READY_CODES)
        if root.findtext("ErrorCode") in _NOT_READY_CODES:
            logger.debug("Statement not ready yet: %s", root.findtext("ErrorMessage"))
            return ""
        msg = f"Unexpected XML response format: {content}"
        raise BrokerError(self.id, BrokerFailure.UNEXPECTED_RESPONSE, msg)

    def _get(self, session: requests.Session, url: str, params: dict[str, str]) -> str:
        """Send one request, returning the response body."""
        try:
            response = session.get(f"{url}?{urlencode(params)}", timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            raise BrokerError(
                self.id,
                BrokerFailure.BUSY,
                f"Failed to reach IBKR: {e}",
            ) from e
        logger.debug("IBKR response: %s", response.text)
        return response.text

    def _parse(self, text: str) -> ET.Element:
        """Parse an XML answer."""
        try:
            return ET.fromstring(text)
        except ET.ParseError as e:
            msg = f"Failed to parse XML response: {e}"
            raise BrokerError(self.id, BrokerFailure.UNEXPECTED_RESPONSE, msg) from e

    def _raise_on_failure(
        self,
        root: ET.Element,
        unless: frozenset[str] = frozenset(),
    ) -> None:
        """Raise the failure an XML answer reports, unless its code is in `unless`."""
        code = root.findtext("ErrorCode") or ""
        if root.findtext("Status") != "Fail" or code in unless:
            return
        kind = _FAILURES_BY_CODE.get(code, BrokerFailure.UNEXPECTED_RESPONSE)
        msg = f"IBKR API Error {code}: {root.findtext('ErrorMessage')}"
        raise BrokerError(self.id, kind, msg)

    def _save(self, content: str, name: str) -> tuple[Path, int]:
        """Write a statement into the imports inbox, returning its path and lines."""
        path = get_config().imports_path / name
        lines = content.strip().split("\n")
        try:
            with path.open("w", newline="", encoding="utf-8") as file:
                csv.writer(file).writerows(csv.reader(lines))
        except OSError as e:
            msg = f"Failed to save CSV to {path}: {e}"
            raise BrokerError(self.id, BrokerFailure.NOT_SAVED, msg) from e
        logger.info('Saved %d lines to "%s"', len(lines), path)
        return path, len(lines)


def _preview(title: str, content: str) -> PreviewTable:
    """Preview a statement's first rows exactly as IBKR sent them.

    A statement covering several accounts repeats its header before each one's
    rows; those repeats are not rows of their own.
    """
    header, *rest = csv.reader(content.strip().splitlines())
    rows = [row for row in rest if row and row != header]
    width = len(header)
    return PreviewTable(
        title=title,
        columns=tuple(header),
        rows=tuple(tuple((row + [""] * width)[:width]) for row in rows[:PREVIEW_ROWS]),
        total=len(rows),
    )


def _session() -> requests.Session:
    """Open an HTTP session that retries IBKR's transient failures."""
    session = requests.Session()
    adapter = HTTPAdapter(
        max_retries=Retry(
            total=MAX_RETRIES,
            backoff_factor=RETRY_BACKOFF_FACTOR,
            status_forcelist=RETRY_STATUS_FORCELIST,
            allowed_methods=["GET", "POST"],
        ),
    )
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _flex_end(query_name: str, end: date) -> date:
    """Move a cash activity query ending today or later back to the last weekday.

    IBKR has no cash activity for a day that has not closed yet.
    """
    today = datetime.now(TORONTO_TZ).date()
    if "Activity" not in query_name or end < today:
        return end
    last_weekday = today - timedelta(days=1)
    while last_weekday.weekday() > 4:  # noqa: PLR2004
        last_weekday -= timedelta(days=1)
    logger.info("ADJUSTED: to_date %s -> %s for %s", end, last_weekday, query_name)
    return last_weekday


def _flex_date(day: date) -> str:
    """Spell a date as the Flex Web Service and file names do, YYYYMMDD."""
    return day.strftime("%Y%m%d")
