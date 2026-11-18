"""Tests for the IBKR broker."""

from __future__ import annotations

import itertools
from datetime import date, datetime, tzinfo
from typing import TYPE_CHECKING, Self
from urllib.parse import parse_qs, urlparse

import pytest

from config.brokers import IbkrSettings
from services import BrokerFailure, IbkrBroker, ibkr_broker

from .fixtures.ibkr_mocking import (
    IBKRMockContext,
    get_default_mock_csv,
    get_empty_mock_csv,
)

if TYPE_CHECKING:
    from .conftest import TempContext


def _ignore(_text: str) -> None:
    """Show no progress."""


def _freeze_today(monkeypatch: pytest.MonkeyPatch, today: date) -> None:
    """Make the IBKR broker see `today` as the current day in Toronto."""

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls(today.year, today.month, today.day, 12, tzinfo=tz)

    monkeypatch.setattr(ibkr_broker, "datetime", FrozenDatetime)


def _requested_ranges(urls: list[str]) -> list[tuple[str, str]]:
    """List the from and to dates of every SendRequest, in the order sent."""
    ranges = []
    for url in urls:
        if "SendRequest" in url:
            query = parse_qs(urlparse(url).query)
            ranges.append((query["fd"][0], query["td"][0]))
    return ranges


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
    _freeze_today(monkeypatch, today)
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


def test_an_activity_query_ending_before_it_starts_is_skipped(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a Monday the overlap day is past the last closed weekday, Friday."""
    _freeze_today(monkeypatch, date(2025, 8, 18))
    activity = IbkrBroker(IbkrSettings(queries={"ActivityStatement": "123456"}))
    with temp_ctx(), IBKRMockContext(monkeypatch, get_default_mock_csv()) as mock:
        result = activity.download(date(2025, 8, 18), None, _ignore)

    assert _requested_ranges(mock.sent_urls) == []
    assert result.failures == ()
    mock.assert_no_csv_written()


def test_a_range_longer_than_a_year_is_split_into_requests_of_365_days(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ibkr = IbkrBroker(IbkrSettings(queries={"Trades": "123456"}))
    with temp_ctx(), IBKRMockContext(monkeypatch, get_default_mock_csv()) as mock:
        result = ibkr.download(date(2023, 1, 1), date(2025, 3, 10), _ignore)

    assert _requested_ranges(mock.sent_urls) == [
        ("20230101", "20231231"),
        ("20240101", "20241230"),
        ("20241231", "20250310"),
    ]
    assert len(result.files) == 3


def test_requests_are_spaced_to_stay_within_the_rate_limit(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    sent_at: list[float] = []

    def advance(seconds: float) -> None:
        clock[0] += seconds

    ibkr = IbkrBroker(IbkrSettings(queries={"Trades": "123456", "Cash": "654321"}))
    with temp_ctx(), IBKRMockContext(monkeypatch, get_default_mock_csv()) as mock:
        monkeypatch.setattr("time.sleep", advance)
        monkeypatch.setattr("time.monotonic", lambda: clock[0])
        real_get = ibkr._get  # noqa: SLF001

        def timed_get(session: object, url: str, params: dict[str, str]) -> str:
            if "SendRequest" in url:
                sent_at.append(clock[0])
            return real_get(session, url, params)  # ty: ignore[invalid-argument-type]

        monkeypatch.setattr(ibkr, "_get", timed_get)
        ibkr.download(date(2023, 1, 1), date(2024, 6, 30), _ignore)

    assert len(_requested_ranges(mock.sent_urls)) == 4
    gaps = [later - earlier for earlier, later in itertools.pairwise(sent_at)]
    assert min(gaps) >= ibkr_broker.MIN_SECONDS_BETWEEN_REQUESTS


def test_with_no_ibkr_transactions_history_is_downloaded_a_year_at_a_time(
    temp_ctx: TempContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The walk back stops at the first year that comes back empty."""
    _freeze_today(monkeypatch, date(2025, 8, 14))
    ibkr = IbkrBroker(IbkrSettings(queries={"Trades": "123456"}))
    statements = [get_default_mock_csv(), get_default_mock_csv(), get_empty_mock_csv()]
    with temp_ctx(), IBKRMockContext(monkeypatch, statements) as mock:
        result = ibkr.download(None, None, _ignore)

    assert _requested_ranges(mock.sent_urls) == [
        ("20240815", "20250814"),
        ("20230816", "20240814"),
        ("20220816", "20230815"),
    ]
    # The empty year is not saved.
    assert len(result.files) == 2
    assert len(mock.written_csvs) == 2
