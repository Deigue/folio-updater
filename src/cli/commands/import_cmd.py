"""Import command for the folio CLI.

Handles importing transactions from files with processed/review folder management.
"""

from __future__ import annotations

import shutil
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
from cli.console import console_rule, progress_console_context
from cli.display import THEME_SUCCESS
from exporters import ParquetExporter
from importers import import_transactions
from models import ImportResults

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
        help="Directory with files to import",
    ),
    *,
    verbose: bool = typer.Option(
        False,
        "-v",
        "--verbose",
        help="Display the final imported transactions",
    ),
) -> None:
    """Import transactions into the folio.

    Default behavior: Import all files from the default import directory.
    --file: Import specific file.
    --dir: Import all files from specified directory.
    """
    config = bootstrap.reload_config()

    if file and directory:
        console_error("Cannot specify both --file and --dir options")
        raise typer.Exit(1)

    if not file and not directory:
        # Default behavior: import from default import directory
        _import_directory_and_export(config.imports_path, verbose=verbose)
    elif file:
        file_path = Path(file)
        if not file_path.exists():
            console_error(f"File not found: {file}")
            raise typer.Exit(1)

        _import_file_and_export(file_path, verbose=verbose)
    elif directory:
        dir_path = Path(directory)
        if not dir_path.exists():
            console_error(f"Directory not found: {directory}")
            raise typer.Exit(1)

        _import_directory_and_export(dir_path, verbose=verbose)


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


def _import_single_file_to_db(
    file_path: Path,
    *,
    verbose: bool = False,
) -> ImportResults | None:
    """Import a single file to database."""
    display = TransactionDisplay()

    with (
        ProgressDisplay.spinner_progress("green") as progress,
        progress_console_context(progress.console),
    ):
        task = progress.add_task(f"Importing {file_path.name}...", total=None)

        try:
            config = get_config()
            txn_sheet = config.txn_sheet
            results = import_transactions(
                file_path,
                None,
                txn_sheet,
                with_results=True,
            )
            if not isinstance(results, ImportResults):  # pragma: no cover
                console_error(f"Invalid result type from import: {type(results)}")
                return None
            progress.remove_task(task)
        except (OSError, ValueError, KeyError) as e:
            progress.remove_task(task)
            console_error(f"Error importing {file_path.name}: {e}")
            return None

        display.show_import_summary(file_path.name, results)
        display.show_import_audit(results, verbose=verbose)
        return results


def _import_file_and_export(file_path: Path, *, verbose: bool = False) -> None:
    """Import a single file and export to Parquet."""
    import_result = _import_single_file_to_db(file_path, verbose=verbose)
    num_txns = import_result.imported_count() if import_result else 0
    if num_txns > 0:
        _export_to_parquet()
    else:
        console_warning(f"No transactions imported from {file_path.name}")
    _move_file(file_path)


def _import_directory_and_export(dir_path: Path, *, verbose: bool = False) -> None:
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
    for i, file_path in enumerate(import_files):
        # Add separator between files (after first)
        if i > 0:
            console_rule(style="dim")

        result = _import_single_file_to_db(file_path, verbose=verbose)
        num_txns = result.imported_count() if result else 0
        import_results.append(
            {
                "File": file_path.name,
                "Transactions": num_txns,
                "Status": (
                    "[green]Success[/green]"
                    if num_txns > 0
                    else "[yellow]No data[/yellow]"
                ),
            },
        )
        total_imported += num_txns
        _move_file(file_path)

    # Display summary table
    if import_results:
        console_rule("Import Summary", style=THEME_SUCCESS)
        display = TransactionDisplay()
        display.show_data_table(
            import_results,
            title="Import Summary",
            max_rows=20,
            theme=THEME_SUCCESS,
        )

    if total_imported > 0:
        console_success(f"Total transactions imported: {total_imported}")
        _export_to_parquet()
    else:
        console_warning("No transactions imported")


def _export_to_parquet() -> None:
    """Export transactions to Parquet."""
    try:
        with ProgressDisplay.spinner_progress("green") as progress:
            task = progress.add_task("Exporting to Parquet...", total=None)
            exporter = ParquetExporter()
            exported = exporter.export_transactions()
            progress.remove_task(task)
        console_success(f"Exported {exported} transactions to Parquet")
    except (OSError, ValueError, KeyError) as e:
        console_warning(f"Failed to export to Parquet: {e}")


if __name__ == "__main__":
    app()
