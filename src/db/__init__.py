"""Database module for folio-updater.

This module exports the public API for commonly used database operations.
"""

# Also export module references for less common operations
from db import helpers, queries, schema
from db.formatters import ActionValidationRules

# Import commonly used functions for easy access
from db.pipeline import prepare_transactions
from db.queries import (
    add_column_to_table,
    delete_rows,
    drop_table,
    get_columns,
    get_connection,
    get_distinct_values,
    get_max_value,
    get_min_value,
    get_row_count,
    get_rows,
    get_tables,
    insert_or_replace,
    update_rows,
)
from db.schema import create_fx_table, create_ticker_aliases_table, create_txns_table

__all__ = [
    "ActionValidationRules",
    "add_column_to_table",
    "create_fx_table",
    "create_ticker_aliases_table",
    "create_txns_table",
    "delete_rows",
    "drop_table",
    "get_columns",
    "get_connection",
    "get_distinct_values",
    "get_max_value",
    "get_min_value",
    "get_row_count",
    "get_rows",
    "get_tables",
    "helpers",
    "insert_or_replace",
    "pipeline",
    "prepare_transactions",
    "queries",
    "schema",
    "update_rows",
]
