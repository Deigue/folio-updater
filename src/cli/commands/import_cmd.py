"""Import command for the folio CLI.

Handles importing transactions from files with processed/review folder management.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

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
from db.db import get_connection, get_row_count
from exporters.parquet_exporter import ParquetExporter
from importers.excel_importer import import_transactions

if TYPE_CHECKING:
    from utils.config import Config

app = typer.Typer()


@app.command(name="")
def import_transaction_files(
    file: str | None = typer.Option(
        None,
        "-f",
        "--file",
        help="Specific file to import",
    ),
    directory: str | None = typer.Option(
        None,
        "-d",
        "--dir",
        help=(
            "Directory with files to import. Use 'default' to use default import "
            "directory."
        ),
    ),
) -> None:
    """Import transactions into the folio.

    Default behavior: Import transactions from the configured folio Excel file.
    --file: Import specific file.
    --dir: Import all files from directory.
    """
    config = bootstrap.reload_config()

    if file and directory:
        console_error("Cannot specify both --file and --dir options")
        raise typer.Exit(1)

    if not file and not directory:
        # Default behavior: import from folio excel
        _import_folio(config)
    elif file:
        file_path = Path(file)
        if not file_path.exists():
            console_error(f"File not found: {file}")
            raise typer.Exit(1)

        _import_file_and_export(file_path)

    elif directory:
        if directory == "default":  # pragma: no cover
            dir_path = config.imports_path
        else:
            dir_path = Path(directory)
            if not dir_path.exists():
                console_error(f"Directory not found: {directory}")
                raise typer.Exit(1)

        _import_directory_and_export(dir_path)


def _move_file(file_path: Path) -> None:
    """Move a file to the destination folder."""
    config = get_config()
    processed_path = config.processed_path
    destination = processed_path / file_path.name

    # Handle filename conflicts
    counter = 1
    base_name = file_path.stem
    suffix = file_path.suffix

    while destination.exists():  # pragma: no cover
        destination = processed_path / f"{base_name}_{counter}{suffix}"
        counter += 1

    shutil.move(str(file_path), str(destination))
    console_info(f"Moved {file_path.name} to {processed_path.name}/")


def _import_single_file_to_db(file_path: Path) -> int:
    """Import a single file to database.

    Returns number of transactions_imported.
    """
    display = TransactionDisplay()

    with ProgressDisplay.file_import_progress() as progress:
        task = progress.add_task(f"Importing {file_path.name}...", total=None)

        try:
            config = get_config()
            txn_sheet = config.txn_sheet
            num_txns = import_transactions(file_path, None, txn_sheet)
            progress.remove_task(task)

        except (OSError, ValueError, KeyError) as e:
            progress.remove_task(task)
            console_error(f"Error importing {file_path.name}: {e}")
            return 0

        with get_connection() as conn:
            total_count = get_row_count(conn, "transactions")

        display.show_import_summary(
            file_path.name,
            num_txns,
            total_count,
            success=num_txns > 0,
        )
        return num_txns


def _import_file_and_export(file_path: Path) -> None:
    """Import a single file and export to Parquet."""
    num_txns = _import_single_file_to_db(file_path)

    if num_txns > 0:
        _export_to_parquet()
    else:
        console_warning(f"No transactions imported from {file_path.name}")
    _move_file(file_path)


def _import_directory_and_export(dir_path: Path) -> None:
    """Import all files from directory and export to Parquet."""
    supported_extensions = {".xlsx", ".xls", ".csv"}
    import_files = [
        f
        for f in dir_path.iterdir()
        if f.is_file() and f.suffix.lower() in supported_extensions
    ]

    if not import_files:
        console_error(f"No supported files found in {dir_path}")
        raise typer.Exit(1)

    console_info(f"Found {len(import_files)} files to import")

    # Create summary table for all imports
    import_results = []
    total_imported = 0

    # Import all files to database first
    for file_path in import_files:
        num_txns = _import_single_file_to_db(file_path)
        import_results.append(
            {
                "File": file_path.name,
                "Transactions": num_txns,
                "Status": "✅ Success" if num_txns > 0 else "⚠️  No data",
            },
        )
        total_imported += num_txns
        _move_file(file_path)

    # Display summary table
    if import_results:
        display = TransactionDisplay()
        display.show_data_table(
            import_results,
            title="Import Summary",
            max_rows=20,
        )

    if total_imported > 0:
        console_success(f"Total transactions imported: {total_imported}")
        _export_to_parquet()
    else:
        console_warning("No transactions imported")


def _import_folio(config: Config) -> None:
    """Import transactions from folio Excel file."""
    try:
        folio_path = config.folio_path
        if not folio_path.exists():
            console_error(f"Folio file not found: {folio_path}")
            raise typer.Exit(1)

        with ProgressDisplay.file_import_progress() as progress:
            task = progress.add_task(
                f"Importing from folio: {folio_path.name}...",
                total=None,
            )
            txn_sheet = config.txn_sheet
            num_txns = import_transactions(folio_path, None, txn_sheet)
            progress.remove_task(task)

        if num_txns > 0:
            console_success(f"Successfully imported {num_txns} transactions")
            _export_to_parquet()
        else:
            console_warning("No transactions imported from folio")

    except (OSError, ValueError, KeyError) as e:
        console_error(f"Error importing from folio: {e}")
        raise typer.Exit(1) from e


def _export_to_parquet() -> None:
    """Export transactions to Parquet."""
    try:
        with ProgressDisplay.file_import_progress() as progress:
            task = progress.add_task("Exporting to Parquet...", total=None)
            exporter = ParquetExporter()
            exported = exporter.export_transactions()
            progress.remove_task(task)
        console_success(f"Exported {exported} transactions to Parquet")
    except (OSError, ValueError, KeyError) as e:
        console_warning(f"Failed to export to Parquet: {e}")


if __name__ == "__main__":
    app()
