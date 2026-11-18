"""Interactive Brokers, downloaded through its Flex Web Service.

The flex token is kept in the system keyring.
"""

# ruff: noqa: S314
from __future__ import annotations

import csv
import logging
import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import keyring
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app import get_config
from domain import BrokerId
from domain.dates import today_in_toronto, weekday_on_or_before
from models import DateWindow
from services.broker import (
    PREVIEW_ROWS,
    BrokerError,
    BrokerFailure,
    DownloadResult,
    PartFailure,
    PreviewTable,
    restart_day,
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
    from collections.abc import Iterator
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
# IBKR refuses a statement over more days than this.
MAX_DAYS_PER_REQUEST = 365
# IBKR accepts about 10 requests a minute per token.
MIN_SECONDS_BETWEEN_REQUESTS = 6

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
        self._last_request = float("-inf")

    # -- CREDENTIALS -------------------------------------------------------------

    def sign_in(self) -> None:
        """Ask for the flex token when the keyring holds none and a query needs it."""
        if not self._real_queries() or keyring.get_password(
            KEYRING_SERVICE,
            KEYRING_USERNAME,
        ):
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

    def _real_queries(self) -> dict[str, str]:
        """Return the queries with a real ID, leaving out any still a placeholder."""
        return {
            name: query_id
            for name, query_id in self._settings.queries.items()
            if query_id and query_id not in _PLACEHOLDER_QUERY_IDS
        }

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

        A missing start restarts on the overlap day; with no IBKR transactions
        stored, history is downloaded a year at a time instead. Days beyond
        IBKR's per-request limit are asked for in several requests. A query that
        fails is reported and the rest carry on. A query whose ID is still a
        placeholder is skipped.

        Raises:
            BrokerError: When no query is configured at all.
        """
        if not self._settings.queries:
            raise BrokerError(
                self.id,
                BrokerFailure.NOT_CONFIGURED,
                "No IBKR flex queries configured in config.yaml, under brokers.ibkr",
            )
        queries = self._real_queries()
        for query_name in self._settings.queries.keys() - queries.keys():
            console_warning(f"Skipping {query_name}: No valid query ID configured")
        if not queries:
            return DownloadResult()
        today = today_in_toronto()
        end = end or today
        start = start or restart_day(self.id, today)
        with _session() as session:
            if start is None:
                return DownloadResult.merged(
                    self._download_history(session, queries, end, progress),
                )
            return DownloadResult.merged(
                self._download_window(session, queries, window, progress)
                for window in _windows(start, end)
            )

    def _download_history(
        self,
        session: requests.Session,
        queries: dict[str, str],
        end: date,
        progress: Progress,
    ) -> Iterator[DownloadResult]:
        """Download a year at a time back from `end`, until a year brings nothing."""
        console_info(
            "No IBKR transactions stored: downloading history a year at a time",
        )
        while True:
            window = DateWindow(end - timedelta(days=MAX_DAYS_PER_REQUEST - 1), end)
            result = self._download_window(session, queries, window, progress)
            yield result
            if not result.files:
                return
            end = window.start - timedelta(days=1)

    def _download_window(
        self,
        session: requests.Session,
        queries: dict[str, str],
        window: DateWindow,
        progress: Progress,
    ) -> DownloadResult:
        """Download every query over one window of at most a year."""
        return DownloadResult.merged(
            self._download_query(session, query_name, query_id, window, progress)
            for query_name, query_id in queries.items()
        )

    def _download_query(
        self,
        session: requests.Session,
        query_name: str,
        query_id: str,
        window: DateWindow,
        progress: Progress,
    ) -> DownloadResult:
        """Download one query over one window, saving it unless it holds no rows.

        A query that fails is reported in the result, so the rest carry on.
        """
        to_date = _flex_end(query_name, window.end)
        if to_date < window.start:
            console_warning(
                f"Skipping {query_name}: {window.start} to {to_date} holds no days",
            )
            return DownloadResult()
        progress(f"Downloading {query_name}...")
        try:
            reference_code = self._send_request(
                session,
                query_name,
                query_id,
                window.start,
                to_date,
            )
            msg = (
                f"Waiting {INITIAL_WAIT_SECONDS} seconds to allow "
                "statement to process..."
            )
            progress(msg)
            time.sleep(INITIAL_WAIT_SECONDS)
            content = self._poll(session, reference_code, progress)
            if not _has_rows(content):
                console_info(f"{query_name}: No rows from {window.start} to {to_date}")
                return DownloadResult()
            name = ibkr_file_name(
                query_name,
                _flex_date(window.start),
                _flex_date(window.end),
            )
            path, lines = self._save(content, name)
        except BrokerError as e:
            logger.error("%s: %s", query_name, e)  # noqa: TRY400
            console_error(f"{query_name}: {e}")
            return DownloadResult(failures=(PartFailure(query_name, e),))
        console_success(f"{query_name}: {lines} lines received")
        return DownloadResult(files=(path,), previews=(_preview(query_name, content),))

    def retry(self, reference_code: str, progress: Progress) -> DownloadResult:
        """Download the statement of an earlier request again, by its reference code.

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
        """Ask IBKR to generate a flex query statement, returning its reference code.

        Requests are spaced to stay within IBKR's rate limit.
        """
        wait = self._last_request + MIN_SECONDS_BETWEEN_REQUESTS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
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
    today = today_in_toronto()
    if "Activity" not in query_name or end < today:
        return end
    last_weekday = weekday_on_or_before(today - timedelta(days=1))
    logger.info("ADJUSTED: to_date %s -> %s for %s", end, last_weekday, query_name)
    return last_weekday


def _windows(start: date, end: date) -> Iterator[DateWindow]:
    """Split the days from `start` to `end` into windows IBKR accepts, oldest first.

    A backward window (start after end) comes out alone, for the query to skip.
    """
    while True:
        window_end = min(start + timedelta(days=MAX_DAYS_PER_REQUEST - 1), end)
        yield DateWindow(start, window_end)
        if window_end >= end:
            return
        start = window_end + timedelta(days=1)


def _has_rows(content: str) -> bool:
    """Whether a statement holds any row besides its header rows."""
    rows = list(csv.reader(content.strip().splitlines()))
    return any(row != rows[0] for row in rows[1:])


def _flex_date(day: date) -> str:
    """Spell a date as the Flex Web Service and file names do, YYYYMMDD."""
    return day.strftime("%Y%m%d")
