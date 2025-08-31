"""Tests for excel_importer module."""

import logging
from contextlib import _GeneratorContextManager
from typing import Callable

import pandas as pd
import pytest

from app.app_context import AppContext
from db.db import get_connection
from importers.excel_importer import import_transactions
from mock.folio_setup import ensure_folio_exists
from utils.constants import TXN_ESSENTIALS

logger: logging.Logger = logging.getLogger(__name__)

# Add this at the top after imports
pd.set_option("display.max_columns", None)
pd.set_option("display.width", 200)
pd.set_option("display.max_colwidth", None)


def test_import_transactions(
    temp_config: Callable[..., _GeneratorContextManager[AppContext, None, None]],
) -> None:
    with temp_config() as ctx:
        ensure_folio_exists()
        config = ctx.config
        transactions: int = import_transactions(config.folio_path)
        logger.info("%d transactions imported.", transactions)
        assert transactions > 0

        # Test importing into an empty db.
        config.db_path.unlink()
        transactions = import_transactions(config.folio_path)
        logger.info("%d transactions imported.", transactions)
        assert transactions > 0

        # Remove an essential column from the Excel file and test ValueError
        default_df = pd.read_excel(
            config.folio_path,
            sheet_name=config.transactions_sheet(),
        )
        df: pd.DataFrame = default_df.copy()
        # Remove the first essential column
        essential_to_remove = next(iter(TXN_ESSENTIALS))
        if essential_to_remove in df.columns:
            df = df.drop(columns=[essential_to_remove])
        else:  # pragma: no cover
            msg = f"Essential column '{essential_to_remove}' is missing."
            raise ValueError(msg)

        temp_path = config.folio_path.parent / "temp_missing_essential.xlsx"
        df.to_excel(temp_path, index=False, sheet_name=config.transactions_sheet())

        with pytest.raises(
            ValueError,
            match=rf"Could not map essential columns: \{{'{essential_to_remove}'\}}\s*",
        ):
            import_transactions(temp_path)

        # Import extra optional columns and verify that they are processed correctly
        # Add new optional columns with some data
        df = default_df.copy()
        extra_cols = {
            "ExtraCol1": ["foo"] * len(default_df),
            "ExtraCol2": [123] * len(default_df),
            "ExtraCol3": pd.date_range("2020-01-01", periods=len(default_df)),
        }
        for col, data in extra_cols.items():
            df[col] = data

        temp_path = config.folio_path.parent / "temp_extra_optional_columns.xlsx"
        df.to_excel(temp_path, index=False, sheet_name=config.transactions_sheet())

        transactions = import_transactions(temp_path)
        logger.info("%d transactions imported.", transactions)
        assert transactions > 0

        # Debug db data-structure
        with get_connection() as conn:
            # Print tables in db.
            tables = pd.read_sql_query(
                "SELECT name FROM sqlite_master WHERE type='table';",
                conn,
            )["name"].tolist()
            logger.debug("Tables in DB: %s", tables)

            # Print contents of each table
            for table in tables:
                logger.debug("=== %s ===", table)
                query = f'SELECT * FROM "{table}"'  # noqa: S608
                df_table = pd.read_sql_query(query, conn)
                # Show all columns and rows for inspection
                logger.debug("\n%s", df_table.to_string(index=False))
