"""Settlement info command for the folio CLI.

Handles querying settlement date information for transactions in the database.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import typer

from app import bootstrap
from app.app_context import get_config
from cli import (
    ProgressDisplay,
    TransactionDisplay,
    console_error,
    console_info,
    console_success,
    console_warning,
)
from db import db
from db.db import get_connection, get_row_count
from exporters.parquet_exporter import ParquetExporter
from importers.excel_importer import import_statements
from utils.constants import Column, Table


def settlement_info(
    file: str | None = typer.Option(
        None,
        "-f",
        "--file",
        help="Path to monthly statement file to import for settlement updates",
    ),
    *,
    import_flag: bool = typer.Option(
        False,  # noqa: FBT003
        "-i",
        "--import",
        help="Import statement files to update settlement dates",
    ),
) -> None:
    """Show settlement date information for transactions in the database.

    Args:
        file: Optional path to monthly statement file to import for settlement updates
        import_flag: Whether to import statement files for settlement updates
    """
    bootstrap.reload_config()
    if file and not import_flag:
        console_error("The --file/-f option only works with --import enabled.")
        raise typer.Exit(1)

    if import_flag:
        _handle_statement_import(file)

    _display_settlement_statistics()


def _handle_statement_import(file: str | None) -> None:
    """Handle statement import based on file parameter."""
    if file:
        statement_path = Path(file)
        if not statement_path.exists():
            console_error(f"Statement file '{file}' does not exist.")
            raise typer.Exit(1)
        updates = _import_single_statement(statement_path)
    else:
        updates = _import_statements_from_directory()

    # Updates parquets if any settlement dates were updated
    if updates > 0:
        with ProgressDisplay.file_import_progress() as progress:
            task = progress.add_task("Exporting to Parquet...", total=None)
            parquet_exporter = ParquetExporter()
            parquet_exporter.export_all()
            progress.remove_task(task)
        console_success("Exported updated transactions to Parquet.")
    console_info("")


def _import_single_statement(statement_path: Path) -> int:
    """Import a single statement file."""
    with ProgressDisplay.file_import_progress() as progress:
        task = progress.add_task(f"Importing {statement_path.name}...", total=None)
        updates = import_statements(statement_path)
        progress.remove_task(task)

    if updates > 0:
        console_success(
            f"Updated {updates} settlement dates from {statement_path.name}",
        )
    else:
        console_warning(
            f"No settlement dates updated from {statement_path.name}",
        )

    return updates


def _import_statements_from_directory() -> int:
    """Import all statement files from the statements directory."""
    config = get_config()
    statements_dir = config.statements_path

    if not statements_dir.exists():
        console_error(f"Statements directory '{statements_dir}' does not exist.")
        raise typer.Exit(1)

    xlsx_files = list(statements_dir.glob("*.xlsx"))
    csv_files = list(statements_dir.glob("*.csv"))
    statement_files = xlsx_files + csv_files

    if not statement_files:
        console_error(
            f"No statement files (.xlsx or .csv) found in '{statements_dir}'.",
        )
        raise typer.Exit(1)

    console_info(
        f"Found {len(statement_files)} statement file(s) in '{statements_dir}'",
    )

    total_updates = 0
    import_results = []

    for statement_file in statement_files:
        with ProgressDisplay.file_import_progress() as progress:
            task = progress.add_task(f"Importing {statement_file.name}...", total=None)
            updates = import_statements(statement_file)
            progress.remove_task(task)

        total_updates += updates
        status = "✅ Success" if updates > 0 else "⚠️  No updates"
        import_results.append(
            {
                "File": statement_file.name,
                "Updates": updates,
                "Status": status,
            },
        )

        if updates > 0:
            console_success(
                f"Updated {updates} settlement dates from {statement_file.name}",
            )
        else:
            console_warning(f"No settlement dates updated from {statement_file.name}")

    # Show summary table
    if import_results:
        display = TransactionDisplay()
        display.show_data_table(
            import_results,
            title="Statement Import Summary",
            max_rows=20,
        )

    console_info(
        f"TOTAL: Updated {total_updates} settlement dates across "
        f"{len(statement_files)} files.",
    )
    return total_updates


def _display_settlement_statistics() -> None:
    """Display settlement date statistics for transactions."""
    display = TransactionDisplay()

    try:
        with get_connection() as conn:
            # Get total number of transactions with calculated settlement dates
            calculated_count = get_row_count(
                conn,
                Table.TXNS,
                condition=f'"{Column.Txn.SETTLE_CALCULATED}" = 1',
            )

            # Get total number of transactions
            total_count = get_row_count(conn, Table.TXNS)

            # Show statistics panel
            stats: dict[str, int | str] = {
                "Total transactions": total_count,
                "Calculated settlement dates": calculated_count,
                "Provided settlement dates": total_count - calculated_count,
            }
            display.show_stats_panel(stats)

            if calculated_count > 0:
                df = db.get_rows(
                    conn,
                    Table.TXNS,
                    condition=f'"{Column.Txn.SETTLE_CALCULATED}" = 1',
                    order_by=(
                        f'"{Column.Txn.TXN_DATE}" DESC, "{Column.Txn.TXN_ID}" DESC'
                    ),
                )

                # Display with Rich table
                display.show_transactions_table(
                    df,
                    title="Transactions with Calculated Settlement Dates",
                )
            else:
                console_warning(
                    "No transactions found with calculated settlement dates.",
                )

    except sqlite3.DatabaseError as e:
        console_error(f"Database error querying settlement info: {e}")
