"""Symbol command for the folio CLI.

Handles managing ticker aliases: the `OldTicker -> NewTicker` renames the
cost-base engine resolves through.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import typer

from app import audit, bootstrap
from app.audit import AuditMark
from db import (
    backup_folio,
    create_ticker_aliases_table,
    delete_rows,
    get_connection,
    get_rows,
    get_tables,
    insert_or_replace,
)
from domain import Column, Table
from term import console_error, console_info, console_success, console_warning
from ui.widgets import show_data_table

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

logger = logging.getLogger(__name__)


def _describe_alias(alias: dict) -> str:
    """Name an alias on one audit row: `OLD -> NEW from DATE`."""
    return (
        f"{alias[Column.Aliases.OLD_TICKER]} -> {alias[Column.Aliases.NEW_TICKER]}"
        f" from {alias[Column.Aliases.EFFECTIVE_DATE]}"
    )


def _existing_alias(conn: sqlite3.Connection, old_ticker: str) -> dict | None:
    """Return the alias `old_ticker` already has, if any."""
    rows = get_rows(
        conn,
        Table.TICKER_ALIASES,
        where=f'"{Column.Aliases.OLD_TICKER}" = ?',
        params=[old_ticker],
    )
    return None if rows.empty else rows.iloc[0].to_dict()


def _alias_changes(previous: dict, alias: dict) -> str:
    """List what `alias` changes on the one already stored, as `Field old -> new`."""
    return "; ".join(
        f"{column} {previous[column]} -> {alias[column]}"
        for column in (Column.Aliases.NEW_TICKER, Column.Aliases.EFFECTIVE_DATE)
        if str(previous[column]) != str(alias[column])
    )


def _log_alias_write(previous: dict | None, alias: dict, backup: Path | None) -> str:
    """Write the stage and row for a stored alias, and return what the add did."""
    where = audit.describe_backup(backup)
    if previous is None:
        audit.stage("STORE 1 alias; %s", where)
        audit.row(AuditMark.STORED, _describe_alias(alias))
        return "1 alias added"
    audit.stage("REPLACE 1 alias; %s", where)
    changes = _alias_changes(previous, alias)
    audit.row(AuditMark.CHANGED, f"{alias[Column.Aliases.OLD_TICKER]}: {changes}")
    return "1 alias replaced"


def add_alias(old_ticker: str, new_ticker: str, effective_date: str) -> None:
    """Add or update a ticker alias in the database."""
    console_info(
        f"Adding alias: {old_ticker} -> {new_ticker} (effective {effective_date})",
    )
    alias = {
        Column.Aliases.OLD_TICKER: old_ticker.upper(),
        Column.Aliases.NEW_TICKER: new_ticker.upper(),
        Column.Aliases.EFFECTIVE_DATE: effective_date,
    }
    with audit.entry("ADD ALIAS %s", alias[Column.Aliases.OLD_TICKER]) as entry:
        try:
            with get_connection() as conn:
                tables = get_tables(conn)
                if Table.TICKER_ALIASES not in tables:
                    msg = f"{Table.TICKER_ALIASES} table not found, creating it now."
                    logger.debug(msg)
                    create_ticker_aliases_table()

                previous = _existing_alias(conn, alias[Column.Aliases.OLD_TICKER])
                if previous is not None and not _alias_changes(previous, alias):
                    audit.stage("KEEP 1 alias, already as given")
                    audit.row(AuditMark.UNCHANGED, _describe_alias(alias))
                    entry.done("alias unchanged")
                    console_success("Alias already set as given.")
                    return

                backup = backup_folio()
                if not insert_or_replace(conn, Table.TICKER_ALIASES, alias):
                    console_error("Failed to add alias.")
                    entry.failed("the table refused the alias")
                    return
            entry.done(_log_alias_write(previous, alias, backup))
            console_success("Successfully added alias.")
        except OSError as e:
            console_error(f"Error adding alias: {e}")
            entry.failed(e)


def list_aliases() -> None:
    """List all ticker aliases from the database."""
    try:
        with get_connection() as conn:
            tables = get_tables(conn)
            if Table.TICKER_ALIASES not in tables:
                console_info("No ticker aliases found.")
                return

            df = get_rows(
                conn,
                Table.TICKER_ALIASES,
                order_by=f"{Column.Aliases.EFFECTIVE_DATE} DESC",
            )

        if df.empty:
            console_info("No ticker aliases found.")
        else:
            records = [
                {str(k): v for k, v in record.items()}
                for record in df.to_dict("records")
            ]
            show_data_table(records, title="Ticker Aliases", max_rows=20)

    except OSError as e:
        console_error(f"Error listing aliases: {e}")


def delete_alias(old_ticker: str) -> None:
    """Remove a ticker alias from the database."""
    console_info(f"Deleting alias for {old_ticker}")
    msg = f"Alias for '{old_ticker}' not found."
    with audit.entry("REMOVE ALIAS %s", old_ticker.upper()) as entry:
        try:
            with get_connection() as conn:
                tables = get_tables(conn)
                previous = (
                    _existing_alias(conn, old_ticker.upper())
                    if Table.TICKER_ALIASES in tables
                    else None
                )
                if previous is None:
                    console_warning(msg)
                    entry.done("nothing removed: no alias for %s", old_ticker.upper())
                    return

                backup = backup_folio()
                delete_rows(
                    conn,
                    Table.TICKER_ALIASES,
                    where=f'"{Column.Aliases.OLD_TICKER}" = ?',
                    params=[old_ticker.upper()],
                )
            audit.stage("REMOVE 1 alias; %s", audit.describe_backup(backup))
            audit.row(AuditMark.REMOVED, _describe_alias(previous))
            entry.done("1 alias removed")
            console_success(f"Successfully deleted alias for '{old_ticker}'.")
        except OSError as e:
            console_error(f"Error deleting alias: {e}")
            entry.failed(e)


def manage_symbols(
    add: tuple[str, str, str] | None,
    delete: str | None,
    *,
    list_all: bool,
) -> None:
    """Manage all ticker alias operations."""
    options_count = sum([bool(add), bool(delete), list_all])
    if options_count != 1:
        console_error(
            "[italic]--add[/italic], [italic]--list[/italic], and"
            " [italic]--delete[/italic] are mutually exclusive.",
        )
        raise typer.Exit(1)

    bootstrap.reload_config()
    if add:
        add_alias(old_ticker=add[0], new_ticker=add[1], effective_date=add[2])
    elif list_all:
        list_aliases()
    elif delete:
        delete_alias(old_ticker=delete)
