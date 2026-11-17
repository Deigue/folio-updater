"""Shared helpers for folio CLI commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import typer

from app import get_config
from cli.selection import Selection, SelectionError, select_transactions
from db import txns
from domain import AccountType, BrokerId, Grain, parse_account_type
from domain.dates import today_in_toronto
from engine.accounts import FOLIO, Pool
from engine.ledger import LedgerUnavailableError, open_ledger
from engine.pools import FolioValuation
from engine.since_update import record_update
from engine.update_review import split_remaining
from exporters import ParquetExporter
from exporters.output import SingleSheetError, UnsupportedExportError, write_export
from importers import import_statements
from models import StatementsRead
from services.broker_files import (
    SUPPORTED_SUFFIXES,
    archive,
    is_supported,
    statement_month_of,
)
from term import console_error, console_info, console_success, console_warning
from term.progress import spinner
from ui.views.imports import (
    show_import_audit,
    show_import_summary,
    show_settlement_detail,
)
from ui.vocabulary import SPINNER_PARQUET, SPINNER_STATEMENT

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from engine.ledger import Ledger
    from exporters.sheet_spec import Sheet
    from models import StatementImportResult

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


def save_update_baseline() -> None:
    """Save every pool's totals now, for later reports to say what changed since.

    The import that calls this has already stored its rows, so a folio that
    cannot be valued keeps its earlier baseline rather than failing the import.
    """
    try:
        ledger = open_ledger(priced=True)
    except LedgerUnavailableError as error:
        console_warning(f"Kept the earlier update baseline: {error}")
        return
    record_update(FolioValuation.build(ledger), [])


def resolve_pool(
    account: str | None,
    account_type: str | None,
    *,
    default_type: str | None = None,
) -> Pool:
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
        return Pool(Grain.ACCOUNT, account)
    requested = account_type or default_type
    if requested is None or requested.strip().lower() == ALL_POOLS:
        return FOLIO

    resolved = parse_account_type(requested)
    if resolved is None:
        console_error(
            f"Unknown account type '{account_type}'. Try one of: "
            f"{ALL_POOLS}, {', '.join(str(member).lower() for member in AccountType)}",
        )
        raise typer.Exit(1)
    return Pool(Grain.TYPE, str(resolved))


def write_or_exit(path: Path, sheets: Sequence[Sheet]) -> Path:
    """Write sheets to the format the path asks for, or say why not and stop.

    Args:
        path: Where to write. A path with no suffix becomes a workbook.
        sheets: The sheets, in the order they should appear.

    Returns:
        The path actually written.

    Raises:
        typer.Exit: If the path names a format that cannot hold the sheets.
    """
    try:
        return write_export(path, sheets)
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

    if not selection.transactions:
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

    total = txns.count()
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


def refuse_unsupported_file(file_path: Path) -> None:
    """Stop a command that was named a file folio cannot read.

    Args:
        file_path: The file named on the command line.

    Raises:
        typer.Exit: When the file's suffix is not supported.
    """
    if not is_supported(file_path.name):
        console_error(
            f"{file_path.name} is not a supported file "
            f"({', '.join(SUPPORTED_SUFFIXES)})",
        )
        raise typer.Exit(1)


# -- STATEMENTS -----------------------------------------------------------------


def read_statements(
    paths: Sequence[Path],
    *,
    verbose: bool = False,
    interactive: bool = True,
) -> StatementsRead:
    """Read and archive statements, then split the calculated dates they left.

    Args:
        paths: The statements to read, in order.
        verbose: Whether to list every statement row and how it was resolved.
        interactive: Whether the transfer audit offers to expand by keypress.

    Returns:
        What each statement did, and the stuck and pending Wealthsimple dates.
    """
    read: list[StatementImportResult] = []
    for path in paths:
        result = import_single_statement(path, verbose=verbose, interactive=interactive)
        archive_statement(result)
        read.append(result)

    months = {m for result in read if (m := statement_month_of(result.path.name))}
    stuck, pending = split_remaining(
        txns.calculated_settlements(BrokerId.WS),
        months,
        today_in_toronto(),
    )
    statements = StatementsRead(read=read, stuck=stuck, pending=pending)
    if statements.changed():
        with spinner(color=SPINNER_STATEMENT) as progress:
            progress.add_task("Exporting to Parquet...", total=None)
            ParquetExporter().export_all()
    return statements


def import_single_statement(
    statement_path: Path,
    *,
    verbose: bool = False,
    interactive: bool = True,
) -> StatementImportResult:
    """Import a single statement file.

    Args:
        statement_path: Statement to import.
        verbose: Whether to list every statement row and how it was resolved.
        interactive: Whether the transfer audit offers to expand by keypress.

    Returns:
        What the statement changed.
    """
    with spinner(color=SPINNER_STATEMENT) as progress:
        progress.add_task(f"Importing {statement_path.name}...", total=None)
        result = import_statements(statement_path)

    if result.settlements_confirmed > 0:
        console_success(
            f"Confirmed {result.settlements_confirmed} settlement dates from "
            f'"{statement_path.name}"',
        )
    elif result.settlement_already_confirmed() and not result.settlement_unplaced():
        console_info(
            f"All {result.settlement_candidates()} row(s) in "
            f'"{statement_path.name}" are already confirmed',
        )
    else:
        console_warning(f'No settlement dates confirmed from "{statement_path.name}"')

    _report_settlement_matching(statement_path.name, result, verbose=verbose)

    if result.transfer_results and result.transfers_created() > 0:
        show_import_summary(statement_path.name, result.transfer_results)
        show_import_audit(
            result.transfer_results,
            verbose=True,
            interactive=interactive,
        )

    if result.transfers_skipped > 0:
        console_info(
            f"Skipped {result.transfers_skipped} cash transfer(s) in "
            f'"{statement_path.name}" already imported as contributions or '
            "withdrawals",
        )

    if result.transfers_rejected > 0:
        console_warning(
            f"{result.transfers_rejected} transfer(s) in "
            f'"{statement_path.name}" could not be created - see logs/audit.log '
            "for details",
        )

    return result


def archive_statement(result: StatementImportResult) -> None:
    """Move a processed statement out of the statements directory.

    Like an imported file, a statement is archived once it has been read,
    whatever it changed. The archive is kept for reference and never read
    again, so a statement whose transfers failed stays where it is: reading
    it again retries them, and its confirmed dates read as already confirmed.

    Args:
        result: What importing the statement did.
    """
    statement_path = result.path
    if result.transfer_error is not None:
        console_warning(
            f'Transfers in "{statement_path.name}" failed ({result.transfer_error}); '
            "the statement is kept out of the archive to be read again",
        )
        return
    config = get_config()
    folder = config.statements_processed_path
    if archive(statement_path, folder) != statement_path:
        console_info(
            f"Moved {statement_path.name} to "
            f"{config.statements_path.name}/{folder.name}/",
        )


def _report_settlement_matching(
    filename: str,
    result: StatementImportResult,
    *,
    verbose: bool,
) -> None:
    """Say what settlement matching could not place, in full when asked.

    Rows that match nothing or match ambiguously used to reach only the
    audit log, which is what let a mis-spelled ticker go unnoticed. The
    count is always printed; `verbose` adds the row-by-row table.

    Args:
        filename: Statement the rows came from.
        result: What the statement import produced.
        verbose: Whether to list every candidate row, matched or not.
    """
    if verbose:
        show_settlement_detail(filename, result.settlement_matches)
        return

    unplaced = result.settlement_unplaced()
    if unplaced:
        console_warning(
            f"{len(unplaced)} of {result.settlement_candidates()} statement row(s) "
            f'in "{filename}" matched no single transaction - rerun with '
            "[bold italic]--verbose[/bold italic] for details",
        )
