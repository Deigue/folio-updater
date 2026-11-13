"""Tests for the IBKR broker."""

from __future__ import annotations

from datetime import date, datetime, tzinfo
from typing import TYPE_CHECKING, Self
from urllib.parse import parse_qs, urlparse

import pytest

from config.brokers import IbkrSettings
from services import BrokerFailure, IbkrBroker, ibkr_broker

from .fixtures.ibkr_mocking import IBKRMockContext, get_default_mock_csv

if TYPE_CHECKING:
    from .conftest import TempContext


def _ignore(_text: str) -> None:
    """Show no progress."""


@pytest.mark.parametrize(
    ("today", "expected_to_date"),
    [
        pytest.param(date(2025, 8, 18), "20250815", id="monday_sends_friday"),
        pytest.param(date(2025, 8, 14), "20250813", id="thursday_sends_wednesday"),
    ],
)
def test_an_activity_query_ending_today_asks_for_the_last_weekday(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
    today: date,
    expected_to_date: str,
) -> None:
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls(today.year, today.month, today.day, 12, tzinfo=tz)

    monkeypatch.setattr(ibkr_broker, "datetime", FrozenDatetime)
    activity = IbkrBroker(IbkrSettings(queries={"ActivityStatement": "123456"}))
    with temp_ctx(), IBKRMockContext(monkeypatch, get_default_mock_csv()) as mock:
        activity.download(date(2025, 8, 1), today, _ignore)

    (send_request,) = [url for url in mock.sent_urls if "SendRequest" in url]
    assert parse_qs(urlparse(send_request).query)["td"] == [expected_to_date]


def test_an_expired_token_fails_its_query_as_expired_credentials(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ibkr = IbkrBroker(IbkrSettings(queries={"Trades": "123456"}))
    with (
        temp_ctx(),
        IBKRMockContext(
            monkeypatch,
            get_default_mock_csv(),
            send_request_error=("1012", "Token has expired."),
        ) as mock,
    ):
        result = ibkr.download(date(2025, 8, 1), date(2025, 8, 8), _ignore)

    assert result.files == ()
    (failure,) = result.failures
    assert failure.part == "Trades"
    assert failure.error.kind is BrokerFailure.CREDENTIALS_EXPIRED
    mock.assert_no_csv_written()
