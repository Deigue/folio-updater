"""Tests for database utility functions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from db import db
from mock.folio_setup import ensure_folio_exists
from utils.constants import Table

if TYPE_CHECKING:
    from contextlib import _GeneratorContextManager

    from app.app_context import AppContext

MOCK_TRANSACTION_COUNT = 50
TEST_ROW_COUNT = 10


def test_get_rows(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test get_rows with various count and which parameters."""
    with temp_config():
        ensure_folio_exists()

        with db.get_connection() as conn:
            all_rows = db.get_rows(conn, Table.TXNS, which=None, n=None)
            total_count = len(all_rows)
            assert total_count == MOCK_TRANSACTION_COUNT

            zero_rows = db.get_rows(conn, Table.TXNS, which=None, n=0)
            assert len(zero_rows) == total_count

            neg_rows = db.get_rows(conn, Table.TXNS, which=None, n=-2)
            assert len(neg_rows) == total_count

            head_rows = db.get_rows(conn, Table.TXNS, which="head", n=TEST_ROW_COUNT)
            assert len(head_rows) == TEST_ROW_COUNT
            # First row should be TxnId 1
            assert head_rows.iloc[0]["TxnId"] == 1

            tail_rows = db.get_rows(conn, Table.TXNS, which="tail", n=TEST_ROW_COUNT)
            assert len(tail_rows) == TEST_ROW_COUNT
            # Last row should be TxnId 50
            assert tail_rows.iloc[-1]["TxnId"] == MOCK_TRANSACTION_COUNT
