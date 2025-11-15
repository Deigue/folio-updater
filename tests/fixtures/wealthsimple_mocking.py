"""Wealthsimple service mocking utilities for tests."""

# ruff: noqa: ARG001, ARG002, ARG003
from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Any, Self

import keyring
import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType


class WealthsimpleMockContext:
    """Context manager for mocking Wealthsimple service interactions."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_accounts: list[dict[str, Any]] | None = None,
        mock_activities: list[dict[str, Any]] | None = None,
    ) -> None:
        """Initialize Wealthsimple mock context.

        Args:
            monkeypatch: pytest monkeypatch fixture
            mock_accounts: List of account dictionaries to return from get_accounts
            mock_activities: List of activity dictionaries to return from get_activities
        """
        self.monkeypatch = monkeypatch
        self.mock_accounts = mock_accounts or get_mock_accounts()
        self.mock_activities = mock_activities or []
        self.written_csvs: dict[str, str] = {}

    def __enter__(self) -> Self:
        """Set up all Wealthsimple mocks."""
        self._setup_api_mocks()
        self._setup_file_mocks()
        self._setup_auth_mocks()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Clean up mocks."""

    def _setup_api_mocks(self) -> None:
        """Set up Wealthsimple API mocking at the ws-api level."""
        outer_self = self

        # Mock the actual WealthsimpleAPI class methods (external dependency)
        class MockWealthsimpleAPI:
            @staticmethod
            def set_user_agent(user_agent: str) -> None:
                """Mock set_user_agent method."""

            @classmethod
            def from_token(
                cls,
                session: MockWSAPISession,
                persist_session_callback: Callable[..., None],
                username: str,
            ) -> MockWealthsimpleAPI:
                """Mock from_token class method."""
                persist_session_callback("session", username)
                return cls()

            def get_accounts(self) -> list[dict[str, Any]]:
                """Mock get_accounts method."""
                return outer_self.mock_accounts

            def get_activities(
                self,
                account_id: str | list[str],
                start_date: object = None,
                end_date: object = None,
                *,
                load_all: bool = False,
            ) -> list[dict[str, Any]]:
                """Mock get_activities method."""
                return outer_self.mock_activities

            def get_statement_transactions(
                self,
                account_id: str,
                period: str,
            ) -> list[Any]:
                """Mock get_statement_transactions method."""
                return outer_self.mock_activities

            def set_security_market_data_cache(
                self,
                getter_fn: object,
                setter_fn: object,
            ) -> None:
                """Mock set_security_market_data_cache method."""

        # Mock WSAPISession for login flows
        class MockWSAPISession:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                """Mock session initialization."""

            @classmethod
            def from_json(cls, _json_str: str) -> MockWSAPISession:
                """Mock WSAPISession.from_json method."""
                return cls()

        self.monkeypatch.setattr(
            "services.wealthsimple_service.WealthsimpleAPI",
            MockWealthsimpleAPI,
        )
        self.monkeypatch.setattr(
            "services.wealthsimple_service.WSAPISession",
            MockWSAPISession,
        )

    def _setup_file_mocks(self) -> None:
        """Set up file I/O mocking to capture CSV writes."""
        outer_self = self

        def mock_path_open(
            path_self: object,
            mode: str,
            newline: str | None = None,
            encoding: str | None = None,
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
            lambda *_args, **_kwargs: "test_username",
        )
        self.monkeypatch.setattr(
            keyring,
            "set_password",
            lambda *_args, **_kwargs: None,
        )

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


def _create_mock_activities(
    activities_data: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Create mock activity data from simplified dictionaries.

    Args:
        activities_data: List of simplified activity dictionaries

    Returns:
        List of full activity dictionaries compatible with ActivityFeedItem.from_dict
    """
    mock_activities = []
    for activity in activities_data:
        # Create a full activity dictionary with all required fields
        full_activity = {
            "accountId": activity.get("account_id", "test-account-1"),
            "occurredAt": activity.get("occurred_at", "2025-10-02T12:00:00-04:00"),
            "amount": activity.get("amount", "100.00"),
            "assetSymbol": activity.get("asset_symbol", "SPY"),
            "assetQuantity": activity.get("asset_quantity", "1.0"),
            "currency": activity.get("currency", "USD"),
            "type": activity.get("type", "buy"),
            "subType": activity.get("sub_type", "market_order"),
            "status": activity.get("status", "posted"),
            "description": activity.get("description", "Test transaction"),
            "amountSign": activity.get("amount_sign", "negative"),
            # All other fields as None/empty defaults
            "aftOriginatorName": None,
            "aftTransactionCategory": None,
            "aftTransactionType": None,
            "canonicalId": None,
            "eTransferEmail": None,
            "eTransferName": None,
            "externalCanonicalId": None,
            "identityId": None,
            "institutionName": None,
            "p2pHandle": None,
            "p2pMessage": None,
            "spendMerchant": None,
            "securityId": None,
            "billPayCompanyName": None,
            "billPayPayeeNickname": None,
            "redactedExternalAccountNumber": None,
            "opposingAccountId": None,
            "strikePrice": None,
            "contractType": None,
            "expiryDate": None,
            "chequeNumber": None,
            "provisionalCreditAmount": None,
            "primaryBlocker": None,
            "interestRate": None,
            "frequency": None,
            "counterAssetSymbol": None,
            "rewardProgram": None,
            "counterPartyCurrency": None,
            "counterPartyCurrencyAmount": None,
            "counterPartyName": None,
            "fxRate": None,
            "fees": None,
            "reference": None,
            "__typename": "ActivityFeedItem",
        }
        mock_activities.append(full_activity)
    return mock_activities


def get_mock_accounts() -> list[dict[str, Any]]:
    """Get mock account data for testing."""
    return [
        {
            "id": "test-account-1",
            "type": "ca_tfsa",
            "description": "Test TFSA",
            "createdAt": "2023-01-01T00:00:00-05:00",
            "currency": "CAD",
            "supportedCurrencies": ["CAD", "USD"],
            "nickname": "TFSA Account",
            "status": "active",
            "branch": "main",
            "archivedAt": None,
            "closedAt": None,
            "cacheExpiredAt": None,
            "requiredIdentityVerification": None,
            "unifiedAccountType": "savings",
            "accountOwnerConfiguration": None,
            "accountFeatures": [],
            "accountOwners": [],
            "linkedAccount": None,
            "financials": {
                "currentCombined": {
                    "id": "test-account-1",
                    "netLiquidationValue": {
                        "amount": "10000.00",
                        "cents": 1000000,
                        "currency": "CAD",
                    },
                    "netDeposits": {
                        "amount": "5000.00",
                        "cents": 500000,
                        "currency": "CAD",
                    },
                    "simpleReturns": {
                        "amount": {
                            "amount": "5000.00",
                            "cents": 500000,
                            "currency": "CAD",
                        },
                        "asOf": "2023-12-31",
                        "rate": "100.0",
                        "referenceDate": "2023-12-31",
                    },
                    "totalDeposits": {
                        "amount": "5000.00",
                        "cents": 500000,
                        "currency": "CAD",
                    },
                    "totalWithdrawals": {
                        "amount": "0.00",
                        "cents": 0,
                        "currency": "CAD",
                    },
                },
            },
            "custodianAccounts": [],
            "number": "12345",
            "__typename": "Account",
        },
        {
            "id": "test-account-2",
            "type": "ca_rrsp",
            "description": "Test RRSP",
            "createdAt": "2023-01-01T00:00:00-05:00",
            "currency": "CAD",
            "supportedCurrencies": ["CAD"],
            "nickname": "RRSP Account",
            "status": "active",
            "branch": "main",
            "archivedAt": None,
            "closedAt": None,
            "cacheExpiredAt": None,
            "requiredIdentityVerification": None,
            "unifiedAccountType": "retirement",
            "accountOwnerConfiguration": None,
            "accountFeatures": [],
            "accountOwners": [],
            "linkedAccount": None,
            "financials": {
                "currentCombined": {
                    "id": "test-account-2",
                    "netLiquidationValue": {
                        "amount": "50000.00",
                        "cents": 5000000,
                        "currency": "CAD",
                    },
                    "netDeposits": {
                        "amount": "30000.00",
                        "cents": 3000000,
                        "currency": "CAD",
                    },
                    "simpleReturns": {
                        "amount": {
                            "amount": "20000.00",
                            "cents": 2000000,
                            "currency": "CAD",
                        },
                        "asOf": "2023-12-31",
                        "rate": "66.7",
                        "referenceDate": "2023-12-31",
                    },
                    "totalDeposits": {
                        "amount": "30000.00",
                        "cents": 3000000,
                        "currency": "CAD",
                    },
                    "totalWithdrawals": {
                        "amount": "0.00",
                        "cents": 0,
                        "currency": "CAD",
                    },
                },
            },
            "custodianAccounts": [],
            "number": "67890",
            "__typename": "Account",
        },
    ]


def get_mock_activities() -> list[dict[str, Any]]:
    """Get mock activity data for testing."""
    return _create_mock_activities(
        [
            # BUY activity to test DIY_BUY action normalization
            {
                "account_id": "test-tfsa-1",
                "occurred_at": "2025-10-02T12:00:00-04:00",
                "amount": "287.74",
                "asset_symbol": "SPY",
                "asset_quantity": "1.0",
                "currency": "USD",
                "type": "DIY_BUY",
                "sub_type": "market_order",
                "status": "posted",
                "description": "SPY - BUY 1.0 SHARES",
                "amount_sign": "negative",
            },
            # SELL activity to test DIY_SELL action normalization
            {
                "account_id": "test-non-registered-1",
                "occurred_at": "2025-10-03T10:30:00-04:00",
                "amount": "150.50",
                "asset_symbol": "VTI",
                "asset_quantity": "0.5",
                "currency": "USD",
                "type": "DIY_SELL",
                "sub_type": "market_order",
                "status": "posted",
                "description": "VTI - SELL 0.5 SHARES",
                "amount_sign": "positive",
            },
            # SPLIT activity to test CORPORATE_ACTION + SUBDIVISION normalization
            {
                "account_id": "test-tfsa-1",
                "occurred_at": "2025-10-04T09:00:00-04:00",
                "amount": "",
                "asset_symbol": "AAPL",
                "asset_quantity": "",
                "currency": "USD",
                "type": "CORPORATE_ACTION",
                "sub_type": "SUBDIVISION",
                "status": "posted",
                "description": "Subdivision: 1.0 -> 4.0 shares of AAPL",
                "amount_sign": "",
            },
            # CONTRIBUTION activity to test INTERNAL_TRANSFER
            {
                "account_id": "test-tfsa-1",
                "occurred_at": "2025-10-05T15:00:00-04:00",
                "amount": "1000.00",
                "asset_symbol": "",
                "asset_quantity": "",
                "currency": "CAD",
                "type": "INTERNAL_TRANSFER",
                "sub_type": "DEPOSIT",
                "status": "posted",
                "description": "Transfer from bank account",
                "amount_sign": "positive",
            },
            # WITHDRAWAL activity to test TRANSFER_OUT
            {
                "account_id": "test-tfsa-1",
                "occurred_at": "2025-10-06T11:00:00-04:00",
                "amount": None,
                "asset_symbol": "",
                "asset_quantity": "",
                "currency": "CAD",
                "type": "INSTITUTIONAL_TRANSFER_INTENT",
                "sub_type": "SOURCE",
                "status": "posted",
                "description": "Transfer to external account",
                "amount_sign": "negative",
            },
            # Test edge cases: decimal normalization, price calculation
            {
                "account_id": "test-tfsa-1",
                "occurred_at": "2025-10-07T14:00:00-04:00",
                "amount": "123.45",
                "asset_symbol": "XIC.TO",  # Test Canadian ticker
                "asset_quantity": "4.1",
                "currency": "CAD",
                "type": "DIY_BUY",
                "sub_type": "market_order",
                "status": "posted",
                "description": "XIC.TO - BUY 4.1 SHARES",
                "amount_sign": "positive",  # Should be made negative for BUY
            },
        ],
    )


def get_expected_wealthsimple_csv() -> str:
    """Get the expected CSV output for Wealthsimple test activities."""
    return (
        "TxnDate,Action,Amount,$,Price,Units,Ticker,Account\r\n"
        "2025-10-02,BUY,-287.74,USD,287.74,1,SPY,WS-TFSA\r\n"
        "2025-10-03,SELL,150.50,USD,301.00,0.5,VTI,WS-PERSONAL\r\n"
        "2025-10-04,SPLIT,,USD,1,4,AAPL,WS-TFSA\r\n"
        "2025-10-05,CONTRIBUTION,1000.00,CAD,,,,WS-TFSA\r\n"
        "2025-10-06,WITHDRAWAL,,CAD,,,,WS-TFSA\r\n"
        "2025-10-07,BUY,-123.45,CAD,30.11,4.1,XIC.TO,WS-TFSA\r\n"
    )


def get_mock_statement_transactions() -> list[dict[str, Any]]:
    """Get mock statement transaction data for testing.

    Returns a list of dicts shaped similarly to what the WS API returns.
    """
    return [
        {
            "balance": "1000.00",
            "cashMovement": "-500.00",
            "unit": "10",
            "description": "MSFT - BUY 1.0 SHARES",
            "transactionDate": "2025-10-08T12:00:00-04:00",
            "transactionType": "BUY",
            "__typename": "BrokerageMonthlyStatementTransactions",
        },
    ]


def get_expected_statement_txn_csv() -> str:
    """Get the expected CSV output for Wealthsimple statement transactions."""
    return (
        "date,amount,currency,transaction,description\r\n"
        "2025-10-08,-500.00,CAD,BUY,MSFT - BUY 1.0 SHARES\r\n"
    )
