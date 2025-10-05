"""Import command for the folio CLI.

Handles importing transactions from files with processed/review folder management.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import typer

from app import bootstrap
from app.app_context import get_config
from exporters.transaction_exporter import TransactionExporter
from importers.excel_importer import import_transactions

app = typer.Typer()


@app.command(name="")
def import_transaction_files(
    file: str | None = typer.Option(
        None,
        "-f",
        "--file",
        help="Specific file to import (imports to database and exports to folio)",
    ),
    directory: str | None = typer.Option(
        None,
        "-d",
        "--dir",
        help="Directory with files to import (imports all and exports to folio)",
    ),
) -> None:
    """Import transactions into the folio.

    Default behavior: Import transactions from the configured folio Excel file.
    --file: Import specific file, then export new transactions to folio Excel.
    --dir: Import all files from directory, then export new transactions to folio Excel.
    Files are moved to 'processed' folder on success, 'review' folder on issues.
    """
    config = bootstrap.reload_config()

    # Check for conflicting options
    if file and directory:  # pragma: no cover
        typer.echo("Error: Cannot specify both --file and --dir options", err=True)
        raise typer.Exit(1)

    if not file and not directory:
        # Default behavior: import from config folio path
        try:
            folio_path = config.folio_path
            if not folio_path.exists():
                typer.echo(f"Folio file not found: {folio_path}", err=True)
                raise typer.Exit(1)

            typer.echo(f"Importing transactions from folio: {folio_path}")
            txn_sheet = config.transactions_sheet()
            num_txns = import_transactions(folio_path, None, txn_sheet)
            typer.echo(f"Successfully imported {num_txns} transactions")

        except (OSError, ValueError, KeyError) as e:
            typer.echo(f"Error importing from folio: {e}", err=True)
            raise typer.Exit(1) from e

    elif file:
        # Import specific file
        file_path = Path(file)
        if not file_path.exists():  # pragma: no cover
            typer.echo(f"File not found: {file}", err=True)
            raise typer.Exit(1)

        base_path = file_path.parent  # Same directory as the file
        _import_file_and_export(file_path, base_path)

    elif directory:
        # Import from directory
        dir_path = Path(directory)
        if not dir_path.exists():  # pragma: no cover
            typer.echo(f"Directory not found: {directory}", err=True)
            raise typer.Exit(1)

        base_path = dir_path.parent  # Parent of the import directory
        _import_directory_and_export(dir_path, base_path)


def _create_processed_folder(base_path: Path) -> Path:
    """Ensure target processed folder exists."""
    processed_folder = base_path / "processed"

    processed_folder.mkdir(exist_ok=True)

    return processed_folder


def _move_file(file_path: Path, destination_folder: Path) -> None:
    """Move a file to the destination folder."""
    destination = destination_folder / file_path.name

    # Handle filename conflicts
    counter = 1
    base_name = file_path.stem
    suffix = file_path.suffix

    while destination.exists():  # pragma: no cover
        destination = destination_folder / f"{base_name}_{counter}{suffix}"
        counter += 1

    shutil.move(str(file_path), str(destination))
    typer.echo(f"Moved {file_path.name} to {destination_folder.name}/")


def _import_single_file_to_db(file_path: Path) -> int:
    """Import a single file to database.

    Returns number of transactions_imported.
    """
    try:
        typer.echo(f"Importing {file_path.name}...")
        config = get_config()
        txn_sheet = config.transactions_sheet()
        num_txns = import_transactions(file_path, None, txn_sheet)

    except (OSError, ValueError, KeyError) as e:
        typer.echo(f"Error importing {file_path.name}: {e}", err=True)
        return 0
    else:
        typer.echo(
            f"Successfully imported {num_txns} transactions from {file_path.name}",
        )
        return num_txns


def _import_file_and_export(file_path: Path, base_path: Path) -> None:
    """Import a single file and export to folio."""
    processed_folder = _create_processed_folder(base_path)
    num_txns = _import_single_file_to_db(file_path)

    if num_txns > 0:
        try:
            exporter = TransactionExporter()
            # Check if folio exists to determine export method
            if exporter.folio_path.exists():  # pragma: no cover
                exported = exporter.export_update(num_txns)
                if exported > 0:  # pragma: no branch
                    typer.echo(f"Exported {exported} new transactions to folio Excel")
            else:
                # Folio doesn't exist, create it with all transactions
                exported = exporter.export_full()
                typer.echo(f"Created folio Excel with {exported} transactions")
        except (OSError, ValueError, KeyError) as e:
            typer.echo(f"Warning: Failed to export to folio Excel: {e}", err=True)
    else:  # pragma: no cover
        typer.echo(
            f"No transactions imported from {file_path.name}",
        )
    _move_file(file_path, processed_folder)


def _import_directory_and_export(dir_path: Path, base_path: Path) -> None:
    """Import all files from directory and export to folio."""
    supported_extensions = {".xlsx", ".xls", ".csv"}
    import_files = [
        f
        for f in dir_path.iterdir()
        if f.is_file() and f.suffix.lower() in supported_extensions
    ]

    if not import_files:  # pragma: no cover
        typer.echo(f"No supported files found in {dir_path}")
        raise typer.Exit(1)

    processed_folder = _create_processed_folder(base_path)
    typer.echo(f"Found {len(import_files)} files to import")

    total_imported = 0

    # Import all files to database first
    for file_path in import_files:
        num_txns = _import_single_file_to_db(file_path)
        total_imported += num_txns
        _move_file(file_path, processed_folder)

    typer.echo(f"Total transactions imported: {total_imported}")

    # Export all new transactions to folio
    if total_imported > 0:
        try:
            exporter = TransactionExporter()
            # Check if folio exists to determine export method
            if exporter.folio_path.exists():
                exported = exporter.export_update(num_txns)
                typer.echo(
                    f"Export completed. {exported} new transactions exported to folio",
                )
            else:  # pragma: no cover
                # Folio doesn't exist, create it with all transactions
                exported = exporter.export_full()
                typer.echo(f"Created folio Excel with {exported} transactions")
        except (OSError, ValueError, KeyError) as e:
            typer.echo(f"Warning: Failed to export to folio: {e}", err=True)


if __name__ == "__main__":  # pragma: no cover
    app()
