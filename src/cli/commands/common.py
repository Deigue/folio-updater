"""Shared helpers for folio CLI commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import typer

from app import get_config
from app.logging_setup import audit_footer
from cli.selection import Selection, select_transactions
from db import backup_folio, get_connection, get_max_value, txn_count
from domain import ACCOUNT_TYPE_ALIASES, AccountType, Column, Scope, Table
from engine.panels import FOLIO_VIEW, PoolView
from exporters import ParquetExporter
from services import ForexService
from term import announce, console_error, console_success, console_warning
from term.progress import ProgressDisplay

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# Re-exported for existing CLI callers
__all__ = [
    "PoolView",
    "audit_footer",
    "backup_folio",
    "ensure_fx_coverage",
    "parse_account_type",
    "resolve_pool",
    "txn_count",
]

BULK_WARNING_ROWS = 25
BULK_WARNING_SHARE = 0.5

# The `--type` value that names the portfolio-wide pool rather than one type.
ALL_POOLS = "all"


def ensure_fx_coverage(*, through_today: bool = False) -> None:
    """Top up FX rates before a replay, when config allows it.

    Bounded by the folio's own settle dates rather than by today: a folio that
    already has a rate for every date it needs must not reach for the network
    on every invocation just because the calendar has moved on.

    Args:
        through_today: Cover every rate published up to the latest the
            Bank of Canada has.
    """
    if not get_config().auto_getfx:
        return
    earliest = ForexService.get_earliest_transaction_date()
    if earliest is None:
        return
    latest = None
    if not through_today:
        with get_connection() as conn:
            latest = get_max_value(conn, Table.TXNS, Column.Txn.SETTLE_DATE)
    try:
        ForexService.ensure_coverage(earliest, latest)
    except (OSError, ValueError, KeyError):
        announce.warning("Could not refresh FX rates; using what is stored")


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
        account_type: An account type, or `all` for the portfolio-wide pool,
            when `--type` was given.
        default_type: The type a bare invocation reports, or None to mean the
            whole portfolio.

    Returns:
        The resolved pool.

    Raises:
        typer.Exit: If both `-a` and `-t` were given, or if the requested
            account type is not one the engine knows.
    """
    if account and account_type:
        console_error(
            "Use --account or --type, not both: a dashboard shows one pool.",
        )
        raise typer.Exit(1)

    if account:
        return PoolView(Scope.ACCOUNT, account, account)
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
    return PoolView(Scope.TYPE, str(resolved), str(resolved).replace("_", "-").upper())


def export_to_parquet() -> None:
    """Export transactions to Parquet so generated folios stay in sync."""
    try:
        with ProgressDisplay.spinner("green") as progress:
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
        typer.Exit: If a requested TxnId does not exist, nothing matched, or
            the selection has no bounds and would sweep the whole folio.

    Returns:
        The resolved Selection, guaranteed non-empty.
    """
    selection = select_transactions(terms)

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
        console_error(f"No transactions matched: {selection.query!r}")
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
