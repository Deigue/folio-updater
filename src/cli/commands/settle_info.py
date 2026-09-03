"""Settlement info command for the folio CLI.

Handles querying settlement date information for transactions in the database.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from app import bootstrap, get_config
from db import get_connection, get_row_count, get_rows
from domain import Column, Table, TransactionContext
from exporters import ParquetExporter
from importers import import_statements
from term import (
    console_error,
    console_info,
    console_rule,
    console_success,
    console_warning,
    get_symbol,
)
from term.progress import ProgressDisplay
from ui.views.imports import ImportDisplay
from ui.views.transactions import page_transactions
from ui.widgets import show_data_table, show_stats_panel

if TYPE_CHECKING:
    from models import StatementImportResult


def settlement_info(
    file: str | None = typer.Option(
        None,
        "-f",
        "--file",
        help="Path to monthly statement file to import for settlement updates",
    ),
    *,
    import_flag: bool = typer.Option(
        False,
        "-i",
        "--import",
        help="Import statement files to update settlement dates",
    ),
    verbose: bool = typer.Option(
        False,
        "-v",
        "--verbose",
        help="List every statement row weighed, matched or not",
    ),
) -> None:
    """Show settlement date information for transactions in the database.

    Args:
        file: Optional path to monthly statement file to import for settlement updates
        import_flag: Whether to import statement files for settlement updates
        verbose: Whether to list each statement row and how it was resolved
    """
    bootstrap.reload_config()
    if file and not import_flag:
        console_error(
            "The [bold italic]--file[/bold italic] option only works with"
            " [bold italic]--import[/bold italic] enabled.",
        )
        raise typer.Exit(1)

    if verbose and not import_flag:
        console_error(
            "The [bold italic]--verbose[/bold italic] option only works with"
            " [bold italic]--import[/bold italic] enabled.",
        )
        raise typer.Exit(1)

    if import_flag:
        _handle_statement_import(file, verbose=verbose)

    _display_settlement_statistics(import_flag=import_flag)


def _handle_statement_import(file: str | None, *, verbose: bool = False) -> None:
    """Handle statement import based on file parameter."""
    if file:
        statement_path = Path(file)
        if not statement_path.exists():
            console_error(f'Statement file "{file}" does not exist.')
            raise typer.Exit(1)
        results = [_import_single_statement(statement_path, verbose=verbose)]
    else:
        results = _import_statements_from_directory(verbose=verbose)

    # Updates parquets if any changes were made.
    changed = any(
        r.settlement_updates > 0 or r.transfers_created() > 0 for r in results
    )
    if changed:
        with ProgressDisplay.spinner(color="dark_violet") as progress:
            progress.add_task("Exporting to Parquet...", total=None)
            parquet_exporter = ParquetExporter()
            parquet_exporter.export_all()


def _import_single_statement(
    statement_path: Path,
    *,
    verbose: bool = False,
) -> StatementImportResult:
    """Import a single statement file.

    Args:
        statement_path: Statement to import.
        verbose: Whether to list every statement row and how it was resolved.

    Returns:
        What the statement changed.
    """
    with ProgressDisplay.spinner(color="dark_violet") as progress:
        progress.add_task(f"Importing {statement_path.name}...", total=None)
        result = import_statements(statement_path)

    if result.settlement_updates > 0:
        console_success(
            f"Updated {result.settlement_updates} settlement dates from "
            f'"{statement_path.name}"',
        )
    else:
        console_warning(f'No settlement dates updated from "{statement_path.name}"')

    _report_settlement_matching(statement_path.name, result, verbose=verbose)

    if result.transfer_results and result.transfers_created() > 0:
        display = ImportDisplay()
        display.show_import_summary(statement_path.name, result.transfer_results)
        display.show_import_audit(result.transfer_results, verbose=True)

    if result.transfers_skipped > 0:
        console_info(
            f"Skipped {result.transfers_skipped} cash transfer(s) in "
            f'"{statement_path.name}" already imported as contributions or '
            "withdrawals",
        )

    if result.transfers_rejected > 0:
        console_warning(
            f"{result.transfers_rejected} transfer(s) in "
            f'"{statement_path.name}" could not be created - see import log '
            "for details",
        )

    return result


def _report_settlement_matching(
    filename: str,
    result: StatementImportResult,
    *,
    verbose: bool,
) -> None:
    """Say what settlement matching could not place, in full when asked.

    Rows that match nothing or match ambiguously used to reach only the
    importer log, which is what let a mis-spelled ticker go unnoticed. The
    count is always printed; `verbose` adds the row-by-row table.

    Args:
        filename: Statement the rows came from.
        result: What the statement import produced.
        verbose: Whether to list every candidate row, matched or not.
    """
    if verbose:
        ImportDisplay().show_settlement_detail(filename, result.settlement_matches)
        return

    unplaced = result.settlement_unplaced()
    if unplaced:
        console_warning(
            f"{len(unplaced)} of {result.settlement_candidates()} statement row(s) "
            f'in "{filename}" matched no single transaction - rerun with '
            "[bold italic]--verbose[/bold italic] for details",
        )


def _import_statements_from_directory(
    *,
    verbose: bool = False,
) -> list[StatementImportResult]:
    """Import all statement files from the statements directory.

    Args:
        verbose: Whether to list every statement row and how it was resolved.

    Returns:
        One result per statement file found.
    """
    config = get_config()
    statements_dir = config.statements_path

    if not statements_dir.exists():
        console_error(f'Statements directory "{statements_dir}" does not exist.')
        raise typer.Exit(1)

    xlsx_files = list(statements_dir.glob("*.xlsx"))
    csv_files = list(statements_dir.glob("*.csv"))
    statement_files = xlsx_files + csv_files

    if not statement_files:
        console_error(
            f'No statement files (.xlsx or .csv) found in "{statements_dir}".',
        )
        raise typer.Exit(1)

    console_info(
        f'Found {len(statement_files)} statement file(s) in "{statements_dir}"',
    )

    results: list[StatementImportResult] = []
    summary_rows = []

    for statement_file in statement_files:
        result = _import_single_statement(statement_file, verbose=verbose)
        results.append(result)
        changed = result.settlement_updates > 0 or result.transfers_created() > 0
        status = (
            f"{get_symbol('success')}Success"
            if changed
            else f"{get_symbol('warning')}No updates"
        )
        summary_rows.append(
            {
                "File": statement_file.name,
                "Settle Updates": result.settlement_updates,
                "Unplaced": len(result.settlement_unplaced()),
                "Transfers": result.transfers_created(),
                "Rejected": result.transfers_rejected,
                "Status": status,
            },
        )

    # Show summary table
    if summary_rows:
        console_rule(style="medium_purple3")
        show_data_table(
            summary_rows,
            title="Statement Import Summary",
            max_rows=20,
        )

    total_updates = sum(r.settlement_updates for r in results)
    total_transfers = sum(r.transfers_created() for r in results)
    console_info(
        f"Updated {total_updates} date(s) and created {total_transfers} transfer(s) "
        f"across {len(statement_files)} file(s).",
    )
    return results


def _display_settlement_statistics(*, import_flag: bool = False) -> None:
    """Display settlement date statistics for transactions."""
    if import_flag:
        console_rule(style="medium_purple3")

    try:
        with get_connection() as conn:
            # Get total number of transactions with calculated settlement dates
            calculated_count = get_row_count(
                conn,
                Table.TXNS,
                where=f'"{Column.Txn.SETTLE_CALCULATED}" = ?',
                params=[1],
            )

            # Get total number of transactions
            total_count = get_row_count(conn, Table.TXNS)

            # Show statistics panel
            stats: dict[str, int | str] = {
                "Total transactions": total_count,
                "Calculated settlement dates": calculated_count,
                "Provided settlement dates": total_count - calculated_count,
            }
            show_stats_panel(stats)

            if calculated_count > 0:
                df = get_rows(
                    conn,
                    Table.TXNS,
                    where=f'"{Column.Txn.SETTLE_CALCULATED}" = ?',
                    params=[1],
                    order_by=(
                        f'"{Column.Txn.TXN_DATE}" DESC, "{Column.Txn.TXN_ID}" DESC'
                    ),
                )

                page_transactions(
                    df,
                    "Transactions with Calculated Settlement Dates",
                    30,
                    TransactionContext.SETTLEMENT,
                )
            else:
                console_warning(
                    "No transactions found with calculated settlement dates.",
                )

    except sqlite3.DatabaseError as e:
        console_error(f"Database error querying settlement info: {e}")
