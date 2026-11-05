"""Tests for the IBKR service."""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Self
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from domain import TORONTO_TZ
from services import ibkr_service
from services.ibkr_service import DownloadRequest, IBKRService


def _at(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, tzinfo=TORONTO_TZ)


class _Response:
    """The one answer IBKR gives to a request it accepted."""

    text = (
        "<FlexStatementResponse><Status>Success</Status>"
        "<ReferenceCode>ref123</ReferenceCode></FlexStatementResponse>"
    )

    def raise_for_status(self) -> None:
        """Report success, as an accepted request does."""


@pytest.mark.parametrize(
    ("today", "expected_to_date"),
    [
        pytest.param(_at(2025, 8, 18), "20250815", id="monday_sends_friday"),
        pytest.param(_at(2025, 8, 14), "20250813", id="thursday_sends_wednesday"),
    ],
)
def test_an_activity_query_ending_today_asks_for_the_last_weekday(
    monkeypatch: pytest.MonkeyPatch,
    today: datetime,
    expected_to_date: str,
) -> None:
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            return cls.fromtimestamp(today.timestamp(), tz)

    sent: list[str] = []

    def record_get(_session: object, url: str, timeout: int = 30) -> _Response:  # noqa: ARG001
        sent.append(url)
        return _Response()

    monkeypatch.setattr(ibkr_service, "datetime", FrozenDatetime)
    monkeypatch.setattr(requests.Session, "get", record_get)
    monkeypatch.setattr(IBKRService, "get_token", lambda _self: "token")

    with IBKRService() as ibkr:
        ibkr.send_request(
            DownloadRequest(
                query_name="ActivityStatement",
                query_id="123456",
                from_date="20250801",
                to_date=today.strftime("%Y%m%d"),
            ),
        )

    assert len(sent) == 1
    assert parse_qs(urlparse(sent[0]).query)["td"] == [expected_to_date]
