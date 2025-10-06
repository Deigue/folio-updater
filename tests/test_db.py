"""Tests for database utility functions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import pytest

from db import db
from mock.folio_setup import ensure_folio_exists
from utils.constants import Table

if TYPE_CHECKING:
    from contextlib import _GeneratorContextManager

    from app.app_context import AppContext

MOCK_TRANSACTION_COUNT = 50
TEST_ROW_COUNT = 10


@pytest.mark.parametrize(
    ("which", "n", "expected_len", "check_first", "check_last"),
    [
        (None, 0, MOCK_TRANSACTION_COUNT, 1, MOCK_TRANSACTION_COUNT),
        ("head", TEST_ROW_COUNT, TEST_ROW_COUNT, 1, TEST_ROW_COUNT),
    ],
)
def test_get_rows(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
    which: str | None,
    n: int | None,
    expected_len: int,
    check_first: int | None,
    check_last: int | None,
) -> None:
    """Test get_rows with various which and n parameters."""
    with temp_config():
        ensure_folio_exists()

        with db.get_connection() as conn:
            rows = db.get_rows(conn, Table.TXNS, which=which, n=n)
            assert len(rows) == expected_len

            if check_first is not None:
                assert rows.iloc[0]["TxnId"] == check_first

            if check_last is not None:
                assert rows.iloc[-1]["TxnId"] == check_last
