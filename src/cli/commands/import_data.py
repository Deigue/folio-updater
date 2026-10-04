"""Import command for the folio CLI.

Handles importing transactions from files with processed/review folder management.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import typer

from app import bootstrap, get_config
from cli.commands.common import export_to_parquet
from importers import import_transactions
from models import ImportResults
from term import (
    console_error,
    console_info,
    console_rule,
    console_success,
    console_warning,
)
from term.progress import ProgressDisplay
from ui.views.imports import ImportDisplay
from ui.vocabulary import THEME_SUCCESS
from ui.widgets import show_data_table

app = typer.Typer()

SUPPORTED_EXTENSIONS = frozenset({".xlsx", ".xls", ".csv"})


@dataclass(frozen=True)
class FileImport:
    """Single file import's details.

    Attributes:
        path: The file, where it was when the import ran.
        results: The import's audit, or None when the import failed.
        error: Why the import failed, or None when it succeeded.
    """

    path: Path
    results: ImportResults | None = None
    error: str | None = None

    @property
    def imported(self) -> int:
        """Return how many transactions the file added."""
        return self.results.imported_count() if self.results else 0


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


def import_file(
    file_path: Path,
    *,
    verbose: bool = False,
    interactive: bool = True,
) -> FileImport:
    """Import a single file to the database, moving it to processed on success.

    A file that fails stays where it is, so it can be fixed and imported again.

    Args:
        file_path: The file to import.
        verbose: Whether the audit lists every imported transaction.
        interactive: Whether the audit offers to expand its panels by keypress.

    Returns:
        The file's results, or the error that stopped it.
    """
    display = ImportDisplay()

    with ProgressDisplay.spinner("green") as progress:
        progress.add_task(f"Importing {file_path.name}...", total=None)

        try:
            config = get_config()
            txn_sheet = config.txn_sheet
            results = import_transactions(
                file_path,
                None,
                txn_sheet,
                with_results=True,
            )
        except (OSError, ValueError, KeyError) as e:
            console_error(f"Error importing {file_path.name}: {e}")
            return FileImport(file_path, error=str(e))
        if not isinstance(results, ImportResults):  # pragma: no cover
            error = (
                f"Could not read {file_path.name}: not a readable file, or no "
                f"'{txn_sheet}' sheet (see importer.log)"
            )
            console_error(error)
            return FileImport(file_path, error=error)

        display.show_import_summary(file_path.name, results)
        display.show_import_audit(results, verbose=verbose, interactive=interactive)

    _move_file(file_path)
    return FileImport(file_path, results=results)


def _import_file_and_export(file_path: Path, *, verbose: bool = False) -> None:
    """Import a single file and export to Parquet."""
    outcome = import_file(file_path, verbose=verbose)
    if outcome.imported > 0:
        export_to_parquet()
    else:
        console_warning(f"No transactions imported from {file_path.name}")


def pending_import_files(dir_path: Path) -> list[Path]:
    """Return the importable files waiting in a directory.

    Args:
        dir_path: Directory to look in.

    Returns:
        Every file with a supported extension, in name order.
    """
    return sorted(
        f
        for f in dir_path.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def import_directory(
    dir_path: Path,
    *,
    verbose: bool = False,
    interactive: bool = True,
) -> list[FileImport]:
    """Import every pending file in a directory, then summarize them.

    Args:
        dir_path: Directory holding the files.
        verbose: Whether each audit lists every imported transaction.
        interactive: Whether each audit offers to expand its panels by keypress.

    Returns:
        One entry per file, in the order imported. Empty when there were none.
    """
    import_files = pending_import_files(dir_path)
    if not import_files:
        return []

    console_info(f"Found {len(import_files)} files to import")

    outcomes: list[FileImport] = []
    for i, file_path in enumerate(import_files):
        # Add separator between files (after first)
        if i > 0:
            console_rule(style="dim")
        outcomes.append(
            import_file(file_path, verbose=verbose, interactive=interactive),
        )

    console_rule("Import Summary", style=THEME_SUCCESS)
    show_data_table(
        [
            {
                "File": outcome.path.name,
                "Transactions": outcome.imported,
                "Status": _import_status(outcome),
            }
            for outcome in outcomes
        ],
        title="Import Summary",
        max_rows=20,
        theme=THEME_SUCCESS,
    )
    return outcomes


def _import_status(outcome: FileImport) -> str:
    """Label one file's row in the import summary."""
    if outcome.error is not None:  # pragma: no cover
        return "[red]Failed[/red]"
    if outcome.imported > 0:
        return "[green]Success[/green]"
    return "[yellow]No data[/yellow]"  # pragma: no cover


def _import_directory_and_export(dir_path: Path, *, verbose: bool = False) -> None:
    """Import all files from directory and export to Parquet."""
    outcomes = import_directory(dir_path, verbose=verbose)
    if not outcomes:
        console_error(f"No supported files found in {dir_path}")
        raise typer.Exit(1)

    total_imported = sum(outcome.imported for outcome in outcomes)
    if total_imported > 0:
        console_success(f"Total transactions imported: {total_imported}")
        export_to_parquet()
    else:
        console_warning("No transactions imported")


if __name__ == "__main__":
    app()
