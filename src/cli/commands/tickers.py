"""Tickers command for the folio CLI.

Handles managing ticker aliases.
"""

from __future__ import annotations

import logging

import typer

from app import bootstrap
from cli import (
    console_error,
    console_info,
    console_print,
    console_success,
    console_warning,
    show_data_table,
)
from db import (
    create_ticker_aliases_table,
    delete_rows,
    get_connection,
    get_rows,
    get_tables,
    insert_or_replace,
)
from utils.constants import Column, Table

logger = logging.getLogger(__name__)


def add_alias(old_ticker: str, new_ticker: str, effective_date: str) -> None:
    """Add or update a ticker alias in the database."""
    console_info(
        f"Adding alias: {old_ticker} -> {new_ticker} (effective {effective_date})",
    )
    try:
        with get_connection() as conn:
            tables = get_tables(conn)
            if Table.TICKER_ALIASES not in tables:
                msg = f"{Table.TICKER_ALIASES} table not found, creating it now."
                logger.debug(msg)
                create_ticker_aliases_table()

            data = {
                Column.Aliases.OLD_TICKER: old_ticker.upper(),
                Column.Aliases.NEW_TICKER: new_ticker.upper(),
                Column.Aliases.EFFECTIVE_DATE: effective_date,
            }
            success = insert_or_replace(conn, Table.TICKER_ALIASES, data)
            if success:
                console_success("Successfully added alias.")
            else:
                console_error("Failed to add alias.")
    except OSError as e:
        console_error(f"Error adding alias: {e}")


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
    try:
        with get_connection() as conn:
            msg = f"Alias for '{old_ticker}' not found."
            tables = get_tables(conn)
            if Table.TICKER_ALIASES not in tables:
                console_warning(msg)
                return

            condition = f'"{Column.Aliases.OLD_TICKER}" = "{old_ticker.upper()}"'
            deleted = delete_rows(conn, Table.TICKER_ALIASES, condition)

            if deleted == 0:
                console_warning(msg)
            else:
                console_success(f"Successfully deleted alias for '{old_ticker}'.")
    except OSError as e:
        console_error(f"Error deleting alias: {e}")


def manage_ticker_aliases(
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

    add_alias_arg_count = 3
    bootstrap.reload_config()
    if add:
        if len(add) != add_alias_arg_count:
            console_error(
                "[italic]--add[/italic] requires three arguments: "
                "<OLD_TICKER> <NEW_TICKER> <YYYY-MM-DD>",
            )
            raise typer.Exit(1)
        add_alias(old_ticker=add[0], new_ticker=add[1], effective_date=add[2])
    elif list_all:
        list_aliases()
    elif delete:
        delete_alias(old_ticker=delete)
    else:
        console_print("Usage: folio tickers [--add | --list | --delete]")
        console_info("Use 'folio tickers --help' for more information.")
