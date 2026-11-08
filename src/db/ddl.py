"""SQL text for each table, rendered from its model; opens no connection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import Column, Table
from models import FxRate, Quote, QuoteHistory, TickerAlias, Txn
from models.columns import Kind, StoredColumn, stored_columns

if TYPE_CHECKING:
    from collections.abc import Sequence

    from _typeshed import DataclassInstance

TABLE_MODELS: dict[Table, type[DataclassInstance]] = {
    Table.TXNS: Txn,
    Table.FX: FxRate,
    Table.TICKER_ALIASES: TickerAlias,
    Table.QUOTES: Quote,
    Table.QUOTE_HISTORY: QuoteHistory,
}

# Tables whose every write moves their change token, read by the saved ledger.
WATCHED_TABLES = (Table.TXNS, Table.TICKER_ALIASES)
_WRITE_EVENTS = ("INSERT", "UPDATE", "DELETE")

_DATE_PATTERN = "[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]"
_SQL_TYPES = {
    Kind.TEXT: "TEXT",
    Kind.NUMBER: "NUMERIC(20,10)",
    Kind.INTEGER: "INTEGER",
    Kind.FLAG: "INTEGER",
    Kind.DATE: "TEXT",
}
# An extra Txns column holds the source text as it came.
EXTRA_COLUMN_TYPE = "TEXT"


def model_columns(table: Table) -> list[str]:
    """Return the names of a table's model columns, in table order."""
    return [column.name for column in stored_columns(TABLE_MODELS[table])]


def has_autoincrement(table: Table) -> bool:
    """Whether SQLite numbers a table's rows with a counter that never goes back."""
    return any(column.autoincrement for column in stored_columns(TABLE_MODELS[table]))


def create_table(
    table: Table,
    *,
    name: str | None = None,
    extras: Sequence[str] = (),
) -> str:
    """Render a table's `CREATE TABLE`, optionally renamed and with extra columns."""
    columns = stored_columns(TABLE_MODELS[table])
    keys = [column for column in columns if column.key]
    definitions = [_column(column, sole_key=len(keys) == 1) for column in columns]
    definitions += [f'"{extra}" {EXTRA_COLUMN_TYPE}' for extra in extras]
    if len(keys) > 1:
        key = ", ".join(f'"{column.name}"' for column in keys)
        definitions.append(f"PRIMARY KEY ({key})")
    return _create(name or table, definitions)


def create_revisions() -> str:
    """Render the change-token table's `CREATE TABLE`; it has no model."""
    return _create(
        Table.REVISIONS,
        [
            f'"{Column.Revision.TABLE_NAME}" TEXT PRIMARY KEY',
            f'"{Column.Revision.TOKEN}" TEXT NOT NULL',
        ],
    )


def change_token(table: str) -> str:
    """Render the parameter-free statement giving a table a fresh random token."""
    return (
        f'INSERT OR REPLACE INTO "{Table.REVISIONS}" '
        f'("{Column.Revision.TABLE_NAME}", "{Column.Revision.TOKEN}") '
        f"VALUES ('{table}', hex(randomblob(16)))"
    )


def watch_triggers(table: Table) -> dict[str, str]:
    """Render each trigger that moves a table's token, keyed by trigger name."""
    names = {event: f"{table}_{event.lower()}_revises" for event in _WRITE_EVENTS}
    return {
        name: (
            f'CREATE TRIGGER IF NOT EXISTS "{name}" '
            f'AFTER {event} ON "{table}" BEGIN {change_token(table)}; END'
        )
        for event, name in names.items()
    }


def _column(column: StoredColumn, *, sole_key: bool) -> str:
    """Render one column's definition."""
    name = column.name
    parts = [f'"{name}" {_SQL_TYPES[column.kind]}']
    if column.key and sole_key:
        parts.append(
            "PRIMARY KEY AUTOINCREMENT" if column.autoincrement else "PRIMARY KEY",
        )
    # A sole TEXT key never had NOT NULL, and no write path leaves it blank.
    elif column.required:
        parts.append("NOT NULL")
    checks = []
    if column.kind is Kind.DATE:
        checks.append(f'length("{name}") = 10 AND "{name}" GLOB "{_DATE_PATTERN}"')
    if column.kind is Kind.FLAG:
        checks.append(f'"{name}" IN (0, 1)')
    if column.upper:
        checks.append(f'"{name}" = UPPER("{name}")')
    if column.non_empty:
        checks.append(f'length("{name}") > 0')
    if checks:
        parts.append(f"CHECK({' AND '.join(checks)})")
    return " ".join(parts)


def _create(name: str, definitions: Sequence[str]) -> str:
    """Wrap column definitions in a `CREATE TABLE IF NOT EXISTS`."""
    return f"""
    CREATE TABLE IF NOT EXISTS "{name}" (
        {", ".join(definitions)}
    )
    """
