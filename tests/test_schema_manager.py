"""Tests for schema management related functionality."""

import sqlite3
from contextlib import _GeneratorContextManager
from typing import Callable

import pandas as pd
import pytest

from app.app_context import AppContext
from db.db import get_connection
from db.schema_manager import create_txns_table
from utils.constants import TXN_ESSENTIALS, Table


def test_primary_keys(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    """Test that the transactions table enforces composite primary key constraints.

    - Unique rows can be inserted.
    - Duplicate rows (all PK fields identical) raise an IntegrityError.
    - Rows differing in any PK field are accepted.
    """
    with temp_config({"db_path": "folio.db"}):
        create_txns_table()
        with get_connection() as conn:
            # Insert a base row
            base_row = ["2024-01-01", "BUY", "100", "USD", "10", "10", "AAPL"]
            df = pd.DataFrame([base_row], columns=TXN_ESSENTIALS)
            df.to_sql(Table.TXNS.value, conn, if_exists="append", index=False)

            # Insert a row differing by one PK component (should succeed)
            # Let's change the date (assuming it's part of the PK)
            diff_row = base_row.copy()
            diff_row[0] = "2024-01-02"  # Change date
            df2 = pd.DataFrame([diff_row], columns=TXN_ESSENTIALS)
            df2.to_sql(Table.TXNS.value, conn, if_exists="append", index=False)

            # Insert a row differing by another PK component (should succeed)
            diff_row2 = base_row.copy()
            diff_row2[1] = "SELL"  # Change type
            df3 = pd.DataFrame([diff_row2], columns=TXN_ESSENTIALS)
            df3.to_sql(Table.TXNS.value, conn, if_exists="append", index=False)

            # Attempt to insert an exact duplicate (should fail)
            duplicate_row = pd.DataFrame([base_row], columns=TXN_ESSENTIALS)
            with pytest.raises(sqlite3.IntegrityError):
                duplicate_row.to_sql(
                    Table.TXNS.value,
                    conn,
                    if_exists="append",
                    index=False,
                )

            # Verify only three unique rows exist in the table
            count = pd.read_sql_query(
                f'SELECT COUNT(*) as cnt FROM "{Table.TXNS.value}"',  # noqa: S608
                conn,
            )["cnt"].iloc[0]
            assert count == 3  # noqa: PLR2004
