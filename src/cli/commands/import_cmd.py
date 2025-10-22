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

    if file and directory:  # pragma: no cover
        typer.echo("Error: Cannot specify both --file and --dir options", err=True)
        raise typer.Exit(1)

    if not file and not directory:
        # Default behavior: import from folio excel
        _import_folio(config)
    elif file:
        file_path = Path(file)
        if not file_path.exists():  # pragma: no cover
            typer.echo(f"File not found: {file}", err=True)
            raise typer.Exit(1)

        _import_file_and_export(file_path)

    elif directory:
        if directory == "default":  # pragma: no cover
            dir_path = config.imports_path
        else:
            dir_path = Path(directory)
            if not dir_path.exists():  # pragma: no cover
                typer.echo(f"Directory not found: {directory}", err=True)
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
    typer.echo(f"Moved {file_path.name} to {processed_path.name}/")


def _import_single_file_to_db(file_path: Path) -> int:
    """Import a single file to database.

    Returns number of transactions_imported.
    """
    try:
        typer.echo(f"Importing {file_path.name}...")
        config = get_config()
        txn_sheet = config.txn_sheet
        num_txns = import_transactions(file_path, None, txn_sheet)

    except (OSError, ValueError, KeyError) as e:
        typer.echo(f"Error importing {file_path.name}: {e}", err=True)
        return 0
    else:
        typer.echo(
            f"Successfully imported {num_txns} transactions from {file_path.name}",
        )
        return num_txns


def _import_file_and_export(file_path: Path) -> None:
    """Import a single file and export to Parquet."""
    num_txns = _import_single_file_to_db(file_path)
    typer.echo(f"Successfully imported {num_txns} transactions")
    if num_txns > 0:
        _export_to_parquet()
    else:  # pragma: no cover
        typer.echo(
            f"No transactions imported from {file_path.name}",
        )
    _move_file(file_path)


def _import_directory_and_export(dir_path: Path) -> None:
    """Import all files from directory and export to Parquet."""
    supported_extensions = {".xlsx", ".xls", ".csv"}
    import_files = [
        f
        for f in dir_path.iterdir()
        if f.is_file() and f.suffix.lower() in supported_extensions
    ]

    if not import_files:  # pragma: no cover
        typer.echo(f"No supported files found in {dir_path}")
        raise typer.Exit(1)

    typer.echo(f"Found {len(import_files)} files to import")

    total_imported = 0

    # Import all files to database first
    for file_path in import_files:
        num_txns = _import_single_file_to_db(file_path)
        total_imported += num_txns
        _move_file(file_path)

    typer.echo(f"Total transactions imported: {total_imported}")
    if total_imported > 0:
        _export_to_parquet()
    else:  # pragma: no cover
        typer.echo("No transactions imported")


def _import_folio(config: Config) -> None:
    """Import transactions from folio Excel file."""
    try:
        folio_path = config.folio_path
        if not folio_path.exists():
            typer.echo(f"Folio file not found: {folio_path}", err=True)
            raise typer.Exit(1)

        typer.echo(f"Importing transactions from folio: {folio_path}")
        txn_sheet = config.txn_sheet
        num_txns = import_transactions(folio_path, None, txn_sheet)
        typer.echo(f"Successfully imported {num_txns} transactions")
        if num_txns > 0:
            _export_to_parquet()
        else:  # pragma: no cover
            typer.echo("No transactions imported from folio")

    except (OSError, ValueError, KeyError) as e:
        typer.echo(f"Error importing from folio: {e}", err=True)
        raise typer.Exit(1) from e


def _export_to_parquet() -> None:
    """Export transactions to Parquet."""
    try:
        exporter = ParquetExporter()
        exported = exporter.export_transactions()
        typer.echo(f"✓ Exported {exported} transactions to Parquet")
    except (OSError, ValueError, KeyError) as e:
        typer.echo(f"Warning: Failed to export to Parquet: {e}", err=True)


if __name__ == "__main__":  # pragma: no cover
    app()
