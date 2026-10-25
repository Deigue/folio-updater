"""Schema management for the database.

This module aids in initializing and updating the database schema to match
the application's requirements.
"""

from __future__ import annotations

import logging
import sqlite3

from db.backup import backup_folio
from db.queries import (
    change_token_statement,
    drop_table,
    get_columns,
    get_connection,
    get_revisions,
    get_tables,
    get_triggers,
    refresh_change_token,
)
from domain import (
    SUPPORTED_CURRENCIES,
    Action,
    Column,
    Currency,
    PriceRange,
    QuoteStatus,
    Table,
)

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
            f"({', '.join(repr(str(c)) for c in SUPPORTED_CURRENCIES)}))"
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

REVISIONS_COLUMN_DEFINITIONS = [
    ColumnDefinition(Column.Revision.TABLE_NAME, "TEXT", "PRIMARY KEY"),
    ColumnDefinition(Column.Revision.TOKEN, "TEXT", "NOT NULL"),
]

# Every write to these tables moves that table's change token, which is how the
# saved ledger knows whether it was built from the folio as it stands.
WATCHED_TABLES = (Table.TXNS, Table.TICKER_ALIASES)
_WRITE_EVENTS = ("INSERT", "UPDATE", "DELETE")

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
    ColumnDefinition(Column.Quote.QUOTE_TYPE, "TEXT"),
    *(
        ColumnDefinition(column, NUMERIC_PRECISION)
        for column in (
            Column.Quote.TRAILING_PE,
            Column.Quote.FORWARD_PE,
            Column.Quote.EPS,
            Column.Quote.BETA,
            Column.Quote.DIVIDEND_RATE,
            Column.Quote.DIVIDEND_YIELD,
            Column.Quote.LAST_DIVIDEND,
        )
    ),
    ColumnDefinition(Column.Quote.EX_DIVIDEND_DATE, "TEXT"),
    ColumnDefinition(Column.Quote.EARNINGS_DATE, "TEXT"),
    *(
        ColumnDefinition(column, NUMERIC_PRECISION)
        for column in (
            Column.Quote.HIGH_52,
            Column.Quote.LOW_52,
            Column.Quote.AVG_50,
            Column.Quote.AVG_200,
            Column.Quote.VOLUME,
            Column.Quote.AVG_VOLUME,
            Column.Quote.EXPENSE_RATIO,
            Column.Quote.TOTAL_ASSETS,
        )
    ),
    ColumnDefinition(Column.Quote.CATEGORY, "TEXT"),
    ColumnDefinition(Column.Quote.FUND_FAMILY, "TEXT"),
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

    Raises:
        DatabaseError: If there's an issue with database connection or SQL execution
    """
    _create_table(Table.TXNS, TXN_COLUMN_DEFINITIONS)
    _watch(Table.TXNS)


def create_fx_table() -> None:
    """Create the FX rates table in the database if it doesn't already exist.

    The table uses Date as the PRIMARY KEY to ensure uniqueness per date.

    Raises:
        DatabaseError: If there's an issue with database connection or SQL execution
    """
    _create_table(Table.FX, FX_COLUMN_DEFINITIONS)


def create_ticker_aliases_table() -> None:
    """Create the ticker aliases table in the database if it doesn't already exist.

    The table uses OldTicker as the PRIMARY KEY to ensure a ticker can only be
    renamed once.

    Raises:
        DatabaseError: If there's an issue with database connection or SQL execution
    """
    _create_table(Table.TICKER_ALIASES, ALIASES_COLUMN_DEFINITIONS)
    _watch(Table.TICKER_ALIASES)


QUOTE_HISTORY_COLUMN_DEFINITIONS = [
    ColumnDefinition(Column.QuoteHistory.SYMBOL, "TEXT", "NOT NULL"),
    ColumnDefinition(
        Column.QuoteHistory.RANGE,
        "TEXT",
        (
            f'NOT NULL CHECK("{Column.QuoteHistory.RANGE}" IN '
            f"({', '.join(repr(str(r)) for r in PriceRange)}))"
        ),
    ),
    ColumnDefinition(Column.QuoteHistory.SEQ, "INTEGER", "NOT NULL"),
    ColumnDefinition(Column.QuoteHistory.DATE, "TEXT", "NOT NULL"),
    ColumnDefinition(Column.QuoteHistory.CLOSE, NUMERIC_PRECISION, "NOT NULL"),
    ColumnDefinition(Column.QuoteHistory.FETCHED_AT, "TEXT", "NOT NULL"),
]


def create_quote_history_table() -> None:
    """Create the condensed price history table if it doesn't already exist.

    Each symbol holds a fixed handful of rows per range: an anchor and the
    points of its chart. A refresh replaces a symbol's rows rather than
    appending.

    Raises:
        DatabaseError: If there's an issue with database connection or SQL execution
    """
    key = ", ".join(
        f'"{column}"'
        for column in (
            Column.QuoteHistory.SYMBOL,
            Column.QuoteHistory.RANGE,
            Column.QuoteHistory.SEQ,
        )
    )
    _create_table(
        Table.QUOTE_HISTORY,
        QUOTE_HISTORY_COLUMN_DEFINITIONS,
        f"PRIMARY KEY ({key})",
    )


def create_quotes_table() -> None:
    """Create the market quotes cache table if it doesn't already exist.

    The table uses the folio's own canonical Symbol as the PRIMARY KEY and holds
    one latest quote per symbol, so a refresh replaces rather than appends.

    Raises:
        DatabaseError: If there's an issue with database connection or SQL execution
    """
    expected = [str(col_def.name) for col_def in QUOTES_COLUMN_DEFINITIONS]
    with get_connection() as conn:
        existing = (
            get_columns(conn, Table.QUOTES) if Table.QUOTES in get_tables(conn) else []
        )
    if existing and existing != expected:  # pragma: no cover
        logger.info("Rebuilding the quotes cache for its new columns")
        backup_folio()
        with get_connection() as conn:
            drop_table(conn, Table.QUOTES)

    _create_table(Table.QUOTES, QUOTES_COLUMN_DEFINITIONS)


def ensure_change_tokens() -> None:
    """Give each watched table the folio holds its triggers and a change token.

    This is how a folio from before change tokens upgrades. When nothing is
    missing it only reads the schema and the tokens, over a few connections.
    """
    with get_connection() as conn:
        tables = get_tables(conn)
    for table in WATCHED_TABLES:
        if table in tables:
            _watch(table)


def _watch(table: Table) -> None:
    """Make every write to an existing table move its change token.

    Dropping a table drops its triggers, so a table missing any is new,
    recreated, or from before tokens existed. No saved ledger can be trusted
    against it, so it gets a fresh token as well as its triggers.

    Args:
        table: One of `WATCHED_TABLES`, already created.
    """
    names = {event: f"{table}_{event.lower()}_revises" for event in _WRITE_EVENTS}
    with get_connection() as conn:
        watched = set(names.values()) <= set(get_triggers(conn))
        if watched and table in get_revisions(conn):
            return

    _create_table(Table.REVISIONS, REVISIONS_COLUMN_DEFINITIONS)
    with get_connection() as conn:
        for event, name in names.items():
            conn.execute(
                f'CREATE TRIGGER IF NOT EXISTS "{name}" AFTER {event} ON "{table}" '
                f"BEGIN {change_token_statement(table)}; END",
            )
        refresh_change_token(conn, table)
    logger.debug("WATCH table '%s' for changes", table)


def _create_table(
    table_name: str,
    columns: list[ColumnDefinition],
    *table_constraints: str,
) -> None:
    """Create a table with the given name, columns and table-level constraints."""
    definitions = [column.to_sql() for column in columns] + list(table_constraints)
    sql = f"""
    CREATE TABLE IF NOT EXISTS "{table_name}" (
        {", ".join(definitions)}
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
