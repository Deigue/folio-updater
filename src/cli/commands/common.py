"""Shared helpers for folio CLI commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import typer

from cli.selection import Selection, SelectionError, select_transactions
from db import txn_count
from domain import ACCOUNT_TYPE_ALIASES, AccountType, Grain
from engine.ledger import LedgerUnavailableError, open_ledger
from engine.panels import FOLIO_VIEW, PoolView
from exporters import ParquetExporter
from exporters.output import SingleSheetError, UnsupportedExportError, write_export
from term import console_error, console_success, console_warning
from term.progress import spinner
from ui.vocabulary import SPINNER_PARQUET

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from engine.ledger import Ledger
    from exporters.table import Table

logger = logging.getLogger(__name__)

BULK_WARNING_ROWS = 25
BULK_WARNING_SHARE = 0.5

# The `--type` value that names the folio pool rather than one type.
ALL_POOLS = "all"

# How to get past a stored transaction the engine cannot convert.
FIX_UNCONVERTIBLE = (
    "Correct those transactions with `folio edit`, or remove them with `folio delete`."
)


def require_ledger(
    *,
    refresh: bool = False,
    priced: bool = False,
    offline: bool = False,
) -> Ledger:
    """Open the ledger, or say why it cannot be opened and stop.

    Args:
        refresh: Replay even if the saved ledger is current.
        priced: The report prices the folio, so it needs today's FX rate.
        offline: Never touch the network.

    Returns:
        The ledger.

    Raises:
        typer.Exit: If a stored transaction is in a currency the engine cannot
            convert.
    """
    try:
        return open_ledger(refresh=refresh, priced=priced, offline=offline)
    except LedgerUnavailableError as error:
        console_error(f"{error} {FIX_UNCONVERTIBLE}")
        raise typer.Exit(1) from error


def parse_account_type(value: str) -> AccountType | None:
    """Read a `--type` argument as an `AccountType`.

    Args:
        value: The argument, in any case, using any recognised alias.

    Returns:
        The account type, or None when it is not one the engine knows.
    """
    token = value.strip().upper()
    if token in ACCOUNT_TYPE_ALIASES:
        return ACCOUNT_TYPE_ALIASES[token]
    try:
        return AccountType(token)
    except ValueError:
        return None


def resolve_pool(
    account: str | None,
    account_type: str | None,
    *,
    default_type: str | None = None,
) -> PoolView:
    """Decide which pool a `-a` / `-t` request is asking about.

    Args:
        account: A single broker account, when `--account` was given.
        account_type: An account type, or `all` for the folio pool,
            when `--type` was given.
        default_type: The type a bare invocation reports, or None to mean the
            whole folio.

    Returns:
        The resolved pool.

    Raises:
        typer.Exit: If both `-a` and `-t` were given, or if the requested
            account type is not one the engine knows.
    """
    if account and account_type:
        console_error("Use --account or --type, not both.")
        raise typer.Exit(1)

    if account:
        return PoolView(Grain.ACCOUNT, account, account)
    requested = account_type or default_type
    if requested is None or requested.strip().lower() == ALL_POOLS:
        return FOLIO_VIEW

    resolved = parse_account_type(requested)
    if resolved is None:
        console_error(
            f"Unknown account type '{account_type}'. Try one of: "
            f"{ALL_POOLS}, {', '.join(str(member).lower() for member in AccountType)}",
        )
        raise typer.Exit(1)
    return PoolView(Grain.TYPE, str(resolved), str(resolved).replace("_", "-").upper())


def write_or_exit(path: Path, tables: Sequence[Table]) -> Path:
    """Write tables to the format the path asks for, or say why not and stop.

    Args:
        path: Where to write. A path with no suffix becomes a workbook.
        tables: The tables, in the order their sheets should appear.

    Returns:
        The path actually written.

    Raises:
        typer.Exit: If the path names a format that cannot hold the tables.
    """
    try:
        return write_export(path, tables)
    except (UnsupportedExportError, SingleSheetError) as error:
        console_error(str(error))
        raise typer.Exit(1) from error


def parse_currency_choice[Choice: str](
    requested: str | None,
    choices: tuple[Choice, ...],
) -> Choice:
    """Read a `--currency` argument against the choices a command offers.

    Args:
        requested: The argument, in any case, or None when it was not given.
        choices: What the command accepts. The first is the default.

    Returns:
        The matching choice, spelt as listed.

    Raises:
        typer.Exit: If the argument is not one of the choices.
    """
    wanted = (requested or choices[0]).strip().lower()
    for choice in choices:
        if choice.lower() == wanted:
            return choice
    listed = f"{', '.join(choices[:-1])} or {choices[-1]}"
    console_error(f"Unknown currency '{requested}'. Use {listed}.")
    raise typer.Exit(1)


def export_to_parquet() -> None:
    """Export transactions to Parquet so generated folios stay in sync."""
    try:
        with spinner(SPINNER_PARQUET) as progress:
            progress.add_task("Exporting to Parquet...", total=None)
            exporter = ParquetExporter()
            exported = exporter.export_transactions()
        console_success(f"Exported {exported} transactions to Parquet")
    except (OSError, ValueError, KeyError) as e:
        console_warning(f"Failed to export to Parquet: {e}")


def resolve_selection(terms: Sequence[str], *, verb: str) -> Selection:
    """Resolve selection terms for a mutating command, or abort with a message.

    Args:
        terms: Positional CLI terms - either TxnIds or query terms.
        verb: Selection action e.g. "delete" or "edit".

    Raises:
        typer.Exit: If the query could not run, a requested TxnId does not
            exist, nothing matched, or the selection has no bounds and would
            sweep the whole folio.

    Returns:
        The resolved Selection, guaranteed non-empty.
    """
    try:
        selection = select_transactions(terms)
    except SelectionError as error:
        console_error(str(error))
        raise typer.Exit(1) from error

    if selection.missing_ids:
        missing = ", ".join(str(txn_id) for txn_id in selection.missing_ids)
        console_error(f"No transaction with TxnId {missing}.")
        raise typer.Exit(1)

    if selection.is_unbounded:
        console_error(
            f"Refusing to {verb} every transaction - the selection has no "
            f"filters. Narrow it down, or use limit:N.",
        )
        raise typer.Exit(1)

    if selection.transactions.empty:
        console_error(f"No transactions matched: {selection.describe()}")
        raise typer.Exit(1)

    return selection


def confirm_selection(count: int, *, verb: str, force: bool) -> bool:
    """Confirm a bulk mutation, warning when it covers much of the folio.

    Args:
        count: Number of transactions the command would touch.
        verb: Selection action e.g. "delete" or "edit".
        force: Whether confirmation was waived on the command line.

    Returns:
        True if the mutation should proceed.
    """
    if force:
        return True

    total = txn_count()
    if count > BULK_WARNING_ROWS or (total and count / total > BULK_WARNING_SHARE):
        share = count / total * 100 if total else 0
        console_warning(
            f"This will {verb} {count} of {total} transactions "
            f"({share:.0f}% of the folio).",
        )

    return typer.confirm(
        f"{verb.capitalize()} {count} transaction(s)?",
        default=False,
    )
