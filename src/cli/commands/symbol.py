"""Symbol command for the folio CLI.

Handles managing ticker aliases: the `OldTicker -> NewTicker` renames the
cost-base engine resolves through.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from app import audit, bootstrap
from app.audit import AuditMark
from db import aliases, current_backup
from domain import Column
from models import TickerAlias
from term import console_error, console_info, console_success, console_warning
from ui.widgets import show_data_table

if TYPE_CHECKING:
    from pathlib import Path


def _describe_alias(alias: TickerAlias) -> str:
    """Name an alias on one audit row: `OLD -> NEW from DATE`."""
    return f"{alias.old_ticker} -> {alias.new_ticker} from {alias.effective_date}"


def _alias_changes(previous: TickerAlias, alias: TickerAlias) -> str:
    """List what `alias` changes on the one already stored, as `Field old -> new`."""
    before, after = previous.to_row(), alias.to_row()
    return "; ".join(
        f"{column} {before[column]} -> {after[column]}"
        for column in (Column.Aliases.NEW_TICKER, Column.Aliases.EFFECTIVE_DATE)
        if before[column] != after[column]
    )


def _log_alias_write(
    previous: TickerAlias | None,
    alias: TickerAlias,
    backup: Path | None,
) -> str:
    """Write the stage and row for a stored alias, and return what the add did."""
    where = audit.describe_backup(backup)
    if previous is None:
        audit.stage("STORE 1 alias; %s", where)
        audit.row(AuditMark.STORED, _describe_alias(alias))
        return "1 alias added"
    audit.stage("REPLACE 1 alias; %s", where)
    changes = _alias_changes(previous, alias)
    audit.row(AuditMark.CHANGED, f"{alias.old_ticker}: {changes}")
    return "1 alias replaced"


def add_alias(old_ticker: str, new_ticker: str, effective_date: str) -> None:
    """Add or update a ticker alias in the database."""
    console_info(
        f"Adding alias: {old_ticker} -> {new_ticker} (effective {effective_date})",
    )
    alias = TickerAlias(
        old_ticker=old_ticker.upper(),
        new_ticker=new_ticker.upper(),
        effective_date=effective_date,
    )
    with audit.entry("ADD ALIAS %s", alias.old_ticker) as entry:
        try:
            previous = aliases.find(alias.old_ticker)
            if previous is not None and not _alias_changes(previous, alias):
                audit.stage("KEEP 1 alias, already as given")
                audit.row(AuditMark.UNCHANGED, _describe_alias(alias))
                entry.done("alias unchanged")
                console_success("Alias already set as given.")
                return

            if not aliases.write([alias]):
                console_error("Failed to add alias.")
                entry.failed("the table refused the alias")
                return
            entry.done(_log_alias_write(previous, alias, current_backup()))
            console_success("Successfully added alias.")
        except OSError as e:
            console_error(f"Error adding alias: {e}")
            entry.failed(e)


def list_aliases() -> None:
    """List all ticker aliases from the database."""
    try:
        newest_first = sorted(
            aliases.read(),
            key=lambda alias: alias.effective_date,
            reverse=True,
        )
        if newest_first:
            show_data_table(
                [alias.to_row() for alias in newest_first],
                title="Ticker Aliases",
                max_rows=20,
            )
        else:
            console_info("No ticker aliases found.")

    except OSError as e:
        console_error(f"Error listing aliases: {e}")


def delete_alias(old_ticker: str) -> None:
    """Remove a ticker alias from the database."""
    console_info(f"Deleting alias for {old_ticker}")
    msg = f"Alias for '{old_ticker}' not found."
    with audit.entry("REMOVE ALIAS %s", old_ticker.upper()) as entry:
        try:
            previous = aliases.find(old_ticker.upper())
            if previous is None:
                console_warning(msg)
                entry.done("nothing removed: no alias for %s", old_ticker.upper())
                return

            aliases.delete(old_ticker.upper())
            audit.stage("REMOVE 1 alias; %s", audit.describe_backup(current_backup()))
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
