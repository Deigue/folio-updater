"""Schema management for the database.

This module aids in initializing and updating the database schema to match
the application's requirements.
"""

from __future__ import annotations

import logging
import sqlite3

from db.queries import get_connection
from domain import Action, Column, Currency, QuoteStatus, Table

logger = logging.getLogger(__name__)


# -- COLUMN DEFINITIONS ---------------------------------------------------

class ColumnDefinition:
    """Column definition with type and constraints for database schema."""

    def __init__(self, name: str, sql_type: str, constraints: str = "") -> None:
        """Initialize column definition.

        Args:
            name: Column name
            sql_type: SQL data type (TEXT, REAL, INTEGER)
            constraints: Additional SQL constraints (CHECK, NOT NULL, etc.)
        """
        self.name = name
        self.sql_type = sql_type
        self.constraints = constraints

    def to_sql(self) -> str:
        """Convert to SQL column definition."""
        base = f'"{self.name}" {self.sql_type}'
        if self.constraints:
            return f"{base} {self.constraints}"
        return base


# Date pattern for YYYY-MM-DD format validation
DATE_PATTERN_YYYY_MM_DD = "[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]"

# SQL type for numeric columns with precision
NUMERIC_PRECISION = "NUMERIC(20,10)"

# Column definitions for the Txns table
TXN_COLUMN_DEFINITIONS = [
    ColumnDefinition(
        Column.Txn.TXN_ID,
        "INTEGER",
        "PRIMARY KEY AUTOINCREMENT",
    ),
    ColumnDefinition(
        Column.Txn.TXN_DATE,
        "TEXT",
        (
            f'CHECK(length("{Column.Txn.TXN_DATE}") = 10 AND '
            f'"{Column.Txn.TXN_DATE}" GLOB "{DATE_PATTERN_YYYY_MM_DD}")'
        ),
    ),
    ColumnDefinition(
        Column.Txn.ACTION,
        "TEXT",
        f'CHECK("{Column.Txn.ACTION}" IN ({", ".join(repr(str(a)) for a in Action)}))',
    ),
    ColumnDefinition(Column.Txn.AMOUNT, NUMERIC_PRECISION),
    ColumnDefinition(
        Column.Txn.CURRENCY,
        "TEXT",
        (
            f'CHECK("{Column.Txn.CURRENCY}" IN '
            f"({', '.join(repr(str(c)) for c in Currency)}))"
        ),
    ),
    ColumnDefinition(Column.Txn.PRICE, NUMERIC_PRECISION),
    ColumnDefinition(Column.Txn.UNITS, NUMERIC_PRECISION),
    ColumnDefinition(
        Column.Txn.TICKER,
        "TEXT",
        (
            f'CHECK("{Column.Txn.TICKER}" IS NULL OR ('
            f'"{Column.Txn.TICKER}" = UPPER("{Column.Txn.TICKER}") AND '
            f'length("{Column.Txn.TICKER}") > 0))'
        ),
    ),
    ColumnDefinition(
        Column.Txn.ACCOUNT,
        "TEXT",
        (
            f'CHECK("{Column.Txn.ACCOUNT}" IS NOT NULL AND '
            f'length("{Column.Txn.ACCOUNT}") > 0)'
        ),
    ),
    ColumnDefinition(
        Column.Txn.SETTLE_DATE,
        "TEXT",
        (
            f'CHECK(length("{Column.Txn.SETTLE_DATE}") = 10 AND '
            f'"{Column.Txn.SETTLE_DATE}" GLOB "{DATE_PATTERN_YYYY_MM_DD}")'
        ),
    ),
    ColumnDefinition(
        Column.Txn.SETTLE_CALCULATED,
        "INTEGER",
        f'CHECK("{Column.Txn.SETTLE_CALCULATED}" IN (0, 1))',
    ),
]

FX_COLUMN_DEFINITIONS = [
    ColumnDefinition(
        Column.FX.DATE,
        "TEXT",
        (
            f'PRIMARY KEY CHECK(length("{Column.FX.DATE}") = 10 AND '
            f'"{Column.FX.DATE}" GLOB "{DATE_PATTERN_YYYY_MM_DD}")'
        ),
    ),
    ColumnDefinition(Column.FX.FXUSDCAD, NUMERIC_PRECISION, "NOT NULL"),
    ColumnDefinition(Column.FX.FXCADUSD, NUMERIC_PRECISION, "NOT NULL"),
]

ALIASES_COLUMN_DEFINITIONS = [
    ColumnDefinition(
        Column.Aliases.OLD_TICKER,
        "TEXT",
        "PRIMARY KEY",
    ),
    ColumnDefinition(
        Column.Aliases.NEW_TICKER,
        "TEXT",
        "NOT NULL",
    ),
    ColumnDefinition(
        Column.Aliases.EFFECTIVE_DATE,
        "TEXT",
        (
            f'NOT NULL CHECK(length("{Column.Aliases.EFFECTIVE_DATE}") = 10 AND '
            f'"{Column.Aliases.EFFECTIVE_DATE}" GLOB "{DATE_PATTERN_YYYY_MM_DD}")'
        ),
    ),
]

QUOTES_COLUMN_DEFINITIONS = [
    ColumnDefinition(
        Column.Quote.SYMBOL,
        "TEXT",
        (
            f'PRIMARY KEY CHECK("{Column.Quote.SYMBOL}" = '
            f'UPPER("{Column.Quote.SYMBOL}") AND length("{Column.Quote.SYMBOL}") > 0)'
        ),
    ),
    ColumnDefinition(Column.Quote.YSYMBOL, "TEXT", "NOT NULL"),
    ColumnDefinition(Column.Quote.PRICE, NUMERIC_PRECISION),
    ColumnDefinition(Column.Quote.PREV_CLOSE, NUMERIC_PRECISION),
    ColumnDefinition(
        Column.Quote.CURRENCY,
        "TEXT",
        (
            f'CHECK("{Column.Quote.CURRENCY}" IS NULL OR '
            f'"{Column.Quote.CURRENCY}" IN '
            f"({', '.join(repr(str(c)) for c in Currency)}))"
        ),
    ),
    ColumnDefinition(Column.Quote.NAME, "TEXT"),
    ColumnDefinition(Column.Quote.SECTOR, "TEXT"),
    ColumnDefinition(Column.Quote.EXCHANGE, "TEXT"),
    ColumnDefinition(Column.Quote.MARKET_CAP, NUMERIC_PRECISION),
    ColumnDefinition(Column.Quote.QUOTE_TIME, "TEXT"),
    ColumnDefinition(Column.Quote.FETCHED_AT, "TEXT"),
    ColumnDefinition(Column.Quote.META_FETCHED_AT, "TEXT"),
    ColumnDefinition(Column.Quote.SOURCE, "TEXT"),
    ColumnDefinition(
        Column.Quote.STATUS,
        "TEXT",
        (
            f'CHECK("{Column.Quote.STATUS}" IN '
            f"({', '.join(repr(str(s)) for s in QuoteStatus)}))"
        ),
    ),
]


# -- TABLE CREATION -------------------------------------------------------



def create_txns_table() -> None:
    """Create the transactions table in the database if it doesn't already exist.

    The table uses an auto-incrementing TxnId as the PRIMARY KEY to allow
    approved duplicate transactions while maintaining application-level
    duplicate detection on TXN_ESSENTIALS.

    Returns:
        None

    Raises:
        DatabaseError: If there's an issue with database connection or SQL execution
    """
    columns_def = [col_def.to_sql() for col_def in TXN_COLUMN_DEFINITIONS]
    _create_table(Table.TXNS, columns_def)


def create_fx_table() -> None:
    """Create the FX rates table in the database if it doesn't already exist.

    The table uses Date as the PRIMARY KEY to ensure uniqueness per date.

    Returns:
        None

    Raises:
        DatabaseError: If there's an issue with database connection or SQL execution
    """
    columns_def = [col_def.to_sql() for col_def in FX_COLUMN_DEFINITIONS]
    _create_table(Table.FX, columns_def)


def create_ticker_aliases_table() -> None:
    """Create the ticker aliases table in the database if it doesn't already exist.

    The table uses OldTicker as the PRIMARY KEY to ensure a ticker can only be
    renamed once.

    Returns:
        None

    Raises:
        DatabaseError: If there's an issue with database connection or SQL execution
    """
    columns_def = [col_def.to_sql() for col_def in ALIASES_COLUMN_DEFINITIONS]
    _create_table(Table.TICKER_ALIASES, columns_def)


def create_quotes_table() -> None:
    """Create the market quotes cache table if it doesn't already exist.

    The table uses the folio's own canonical Symbol as the PRIMARY KEY and holds
    one latest snapshot per symbol, so a refresh replaces rather than appends.

    Returns:
        None

    Raises:
        DatabaseError: If there's an issue with database connection or SQL execution
    """
    columns_def = [col_def.to_sql() for col_def in QUOTES_COLUMN_DEFINITIONS]
    _create_table(Table.QUOTES, columns_def)


def _create_table(table_name: str, columns_def: list[str]) -> None:
    """Create a table with the given name and column definitions."""
    sql = f"""
    CREATE TABLE IF NOT EXISTS "{table_name}" (
        {", ".join(columns_def)}
    )
    """
    try:
        with get_connection() as conn:
            conn.execute(sql)
            logger.debug("CREATE table '%s'", table_name)
    except sqlite3.DatabaseError as e:
        msg = f"Failed to create table '{table_name}': {e}"
        logger.exception(msg)
        raise
