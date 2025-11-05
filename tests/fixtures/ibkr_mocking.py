"""IBKR service mocking utilities for tests."""

from __future__ import annotations

import csv
import difflib
import io
from typing import TYPE_CHECKING, Self

import keyring
import pytest
import requests

if TYPE_CHECKING:
    from types import TracebackType

BUY_SELL_FIELD = "Buy/Sell"


class IBKRMockContext:
    """Context manager for mocking IBKR service interactions."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_csv_data: str,
        send_request_response: str = "ref123",
        send_request_status: str = "Success",
    ) -> None:
        """Initialize IBKR mock context.

        Args:
            monkeypatch: pytest monkeypatch fixture
            mock_csv_data: CSV data to return from GetStatement
            send_request_response: Reference code to return from SendRequest
            send_request_status: Status to return from SendRequest
        """
        self.monkeypatch = monkeypatch
        self.mock_csv_data = mock_csv_data
        self.send_request_response = send_request_response
        self.send_request_status = send_request_status
        self.written_csvs: dict[str, str] = {}

    def __enter__(self) -> Self:
        """Set up all IBKR mocks."""
        self._setup_http_mocks()
        self._setup_file_mocks()
        self._setup_auth_mocks()
        self._setup_utility_mocks()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Clean up mocks."""

    def _setup_http_mocks(self) -> None:
        """Set up HTTP request mocking."""

        def mock_get(_session_self: object, url: str, timeout: int = 30) -> object:  # noqa: ARG001
            class MockResponse:
                status_code = 200
                text = ""

                def raise_for_status(self) -> None:
                    # Mock implementation - no error handling needed for tests
                    return

            if "SendRequest" in url:
                MockResponse.text = (
                    "<FlexStatementResponse>"
                    f"<Status>{self.send_request_status}</Status>"
                    f"<ReferenceCode>{self.send_request_response}</ReferenceCode>"
                    "</FlexStatementResponse>"
                )
            elif "GetStatement" in url:
                MockResponse.text = self.mock_csv_data
            else:  # pragma: no cover
                MockResponse.text = ""
            return MockResponse()

        self.monkeypatch.setattr(requests.Session, "get", mock_get)

    def _setup_file_mocks(self) -> None:
        """Set up file I/O mocking to capture CSV writes."""
        outer_self = self

        def mock_path_open(
            path_self: object,
            mode: str,
            newline: str | None = None,  # noqa: ARG001
            encoding: str | None = None,  # noqa: ARG001
        ) -> object:
            class MockFile:
                def __init__(self) -> None:
                    self.content = ""

                def write(self, data: str) -> None:
                    self.content += data

                def __enter__(self) -> Self:
                    return self

                def __exit__(
                    self,
                    _exc_type: type[BaseException] | None,
                    _exc_value: BaseException | None,
                    _traceback: TracebackType | None,
                ) -> None:
                    if "w" in mode:
                        outer_self.written_csvs[str(path_self)] = self.content

            return MockFile()

        self.monkeypatch.setattr("pathlib.Path.open", mock_path_open)

    def _setup_auth_mocks(self) -> None:
        """Set up authentication mocking."""
        self.monkeypatch.setattr(
            keyring,
            "get_password",
            lambda *_args, **_kwargs: "test_token",
        )
        self.monkeypatch.setattr(
            keyring,
            "set_password",
            lambda *_args, **_kwargs: None,
        )

    def _setup_utility_mocks(self) -> None:
        """Set up utility function mocking."""
        self.monkeypatch.setattr("time.sleep", lambda _x: None)  # Skip sleep delays

    def assert_csv_written(self, expected_content: str) -> None:
        """Assert that CSV files were written with expected content."""
        assert len(self.written_csvs) > 0, "No CSV files were written"
        for filepath, content in self.written_csvs.items():
            if content != expected_content:  # pragma: no cover
                diff = "\n".join(
                    difflib.unified_diff(
                        content.splitlines(keepends=True),
                        expected_content.splitlines(keepends=True),
                        fromfile="actual",
                        tofile="expected",
                    ),
                )
                pytest.fail(f"CSV content mismatch for {filepath}:\n{diff}")

    def assert_no_csv_written(self) -> None:
        """Assert that no CSV files were written."""
        assert len(self.written_csvs) == 0, (
            f"Expected no CSV files to be written, but found: "
            f"{list(self.written_csvs.keys())}"
        )


def create_mock_csv_data(rows: list[dict[str, str]]) -> str:
    """Create mock CSV data from a list of row dictionaries.

    Args:
        rows: List of dictionaries representing CSV rows

    Returns:
        Formatted CSV string with headers
    """
    if not rows:  # pragma: no cover
        return ""

    headers = list(rows[0].keys())
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)

    writer.writerow(headers)
    for row in rows:
        values = [str(row.get(header, "")) for header in headers]
        writer.writerow(values)

    return output.getvalue()


# Common test data
DEFAULT_CSV_HEADERS = [
    "SettleDate",
    "TradeDate",
    BUY_SELL_FIELD,
    "Proceeds",
    "CurrencyPrimary",
    "Price",
    "Quantity",
    "Commission",
    "AccountAlias",
    "ClientAccountID",
    "Symbol",
    "OtherCommission",
]

DEFAULT_TRANSACTION_ROWS = [
    {
        "SettleDate": "2025-10-03",
        "TradeDate": "2025-10-02",
        BUY_SELL_FIELD: "BUY",
        "Proceeds": "-212.96",
        "CurrencyPrimary": "USD",
        "Price": "287.74",
        "Quantity": "0.8128",
        "Commission": "-0.350438535",
        "AccountAlias": "IBKR-MOCK",
        "ClientAccountID": "12345",
        "Symbol": "SPY",
        "OtherCommission": "0",
    },
    {
        "SettleDate": "2025-10-03",
        "TradeDate": "2025-10-02",
        BUY_SELL_FIELD: "BUY",
        "Proceeds": "0.028475",
        "CurrencyPrimary": "USD",
        "Price": "287.75",
        "Quantity": "0.0001",
        "Commission": "-0.000000022",
        "AccountAlias": "IBKR-MOCK",
        "ClientAccountID": "12345",
        "Symbol": "SPY",
        "OtherCommission": "0",
    },
]


def get_default_mock_csv() -> str:
    """Get default mock CSV data for testing."""
    return create_mock_csv_data(DEFAULT_TRANSACTION_ROWS)
