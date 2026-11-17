"""Tests for downloaded file names and the inboxes they wait in."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from domain import BrokerId
from services.broker_files import (
    broker_of_file,
    date_range_of,
    ibkr_file_name,
    ibkr_retry_file_name,
    statement_account_of,
    statement_month_of,
    waiting_files,
    ws_activities_file_name,
    ws_statement_file_name,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_names_keep_the_format_existing_downloads_use() -> None:
    assert (
        ibkr_file_name("TradeConfirmation", "20260105", "20260206")
        == "ibkr_TradeConfirmation_20260105_20260206.csv"
    )
    assert ibkr_retry_file_name("7654321") == "ibkr_ref_7654321.csv"
    assert (
        ws_activities_file_name("20260105", "20260206")
        == "ws_activities_20260105_20260206.csv"
    )
    assert (
        ws_statement_file_name("WS-TEST", "2026-01")
        == "ws_statement_WS-TEST_202601.csv"
    )


@pytest.mark.parametrize(
    ("name", "broker"),
    [
        (ibkr_file_name("TradeConfirmation", "20260105", "20260206"), BrokerId.IBKR),
        (ibkr_retry_file_name("7654321"), BrokerId.IBKR),
        (ws_activities_file_name("20260105", "20260206"), BrokerId.WS),
        (ws_statement_file_name("WS-TEST", "2026-01"), BrokerId.WS),
        ("my_transactions.xlsx", None),
    ],
)
def test_a_name_says_which_broker_it_came_from(
    name: str,
    broker: BrokerId | None,
) -> None:
    assert broker_of_file(name) is broker


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            ibkr_file_name("TradeConfirmation", "20260105", "20260206"),
            ("2026-01-05", "2026-02-06"),
        ),
        (
            ws_activities_file_name("20260105", "20260206"),
            ("2026-01-05", "2026-02-06"),
        ),
        (ibkr_retry_file_name("7654321"), None),
        (ws_statement_file_name("WS-TEST", "2026-01"), None),
    ],
)
def test_a_name_gives_back_its_date_range(
    name: str,
    expected: tuple[str, str] | None,
) -> None:
    assert date_range_of(name) == expected


@pytest.mark.parametrize(
    ("name", "account", "month"),
    [
        (ws_statement_file_name("WS-TEST", "2026-01"), "WS-TEST", "2026-01"),
        (
            ws_statement_file_name("WS_WITH_UNDERSCORES", "2026-02"),
            "WS_WITH_UNDERSCORES",
            "2026-02",
        ),
        ("ws_statement_WS-TEST_202601.xlsx", "WS-TEST", "2026-01"),
        ("WS_STATEMENT_WS-TEST_202601.csv", "WS-TEST", "2026-01"),
        ("statement.xlsx", None, None),
    ],
)
def test_a_statement_name_gives_back_its_account_and_month(
    name: str,
    account: str | None,
    month: str | None,
) -> None:
    assert statement_account_of(name) == account
    assert statement_month_of(name) == month


def test_waiting_files_lists_supported_files_in_name_order(tmp_path: Path) -> None:
    for name in ("b.xlsx", "a.csv", "c.CSV", "old.xls", "notes.txt"):
        (tmp_path / name).touch()
    (tmp_path / "processed.csv").mkdir()

    assert [path.name for path in waiting_files(tmp_path)] == [
        "a.csv",
        "b.xlsx",
        "c.CSV",
    ]
