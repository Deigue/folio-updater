"""Persistence layer for folio-updater.

This module exports the API for commonly used database operations.
"""

# Also export module references for less common operations
from db import (
    aliases,
    backup,
    fx_rates,
    helpers,
    queries,
    quote_history,
    quotes,
    revisions,
    schema,
    txns,
)
from db.backup import backup_folio, backup_scope, current_backup
from db.catalog import get_columns, get_tables, get_triggers
from db.helpers import txn_count
from db.queries import (
    add_column_to_table,
    append_rows,
    delete_rows,
    drop_table,
    get_broker_latest_txn_date,
    get_connection,
    get_distinct_set,
    get_distinct_values,
    get_last_insert_rowid,
    get_max_value,
    get_min_value,
    get_row_count,
    get_rows,
    insert_or_replace_many,
    trial_insert_rows,
    update_rows,
)
from db.schema import (
    create_fx_table,
    create_quotes_table,
    create_ticker_aliases_table,
    create_txns_table,
)

__all__ = [
    "add_column_to_table",
    "aliases",
    "append_rows",
    "backup",
    "backup_folio",
    "backup_scope",
    "create_fx_table",
    "create_quotes_table",
    "create_ticker_aliases_table",
    "create_txns_table",
    "current_backup",
    "delete_rows",
    "drop_table",
    "fx_rates",
    "get_broker_latest_txn_date",
    "get_columns",
    "get_connection",
    "get_distinct_set",
    "get_distinct_values",
    "get_last_insert_rowid",
    "get_max_value",
    "get_min_value",
    "get_row_count",
    "get_rows",
    "get_tables",
    "get_triggers",
    "helpers",
    "insert_or_replace_many",
    "queries",
    "quote_history",
    "quotes",
    "revisions",
    "schema",
    "trial_insert_rows",
    "txn_count",
    "txns",
    "update_rows",
]
