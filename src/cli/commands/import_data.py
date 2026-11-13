"""Import command for the folio CLI.

Imports transactions from files, moving each one to the processed folder.
"""

from __future__ import annotations

from pathlib import Path

import typer

from app import bootstrap, get_config
from cli.commands.common import (
    export_to_parquet,
    refuse_unsupported_file,
    save_update_baseline,
)
from datagen import DemoFolio, is_demo_folio
from db import DatabaseError
from importers import import_transactions
from models import FileImport
from services.broker_files import archive, waiting_files
from term import (
    console_error,
    console_info,
    console_rule,
    console_success,
    console_warning,
)
from term.progress import spinner
from ui.views.imports import show_files_summary, show_import_audit, show_import_summary
from ui.vocabulary import SPINNER_IMPORT


def import_transaction_files(
    file: str | None = None,
    directory: str | None = None,
    *,
    verbose: bool = False,
) -> None:
    """Import transactions into the folio.

    Default behavior: Import all files from the default import directory.

    Args:
        file: Import this specific file.
        directory: Import all files from this directory.
        verbose: Display the final imported transactions.

    Raises:
        typer.Exit: On an unusable request.
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
        refuse_unsupported_file(file_path)

        _import_file_and_export(file_path, verbose=verbose)
    elif directory:
        dir_path = Path(directory)
        if not dir_path.exists():
            console_error(f"Directory not found: {directory}")
            raise typer.Exit(1)

        _import_directory_and_export(dir_path, verbose=verbose)


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
        interactive: Whether the audit offers to expand its sections by keypress.

    Returns:
        The file's results, or the error that stopped it.
    """
    with spinner(SPINNER_IMPORT) as progress:
        progress.add_task(f"Importing {file_path.name}...", total=None)

        try:
            config = get_config()
            txn_sheet = config.txn_sheet
            results = import_transactions(file_path, None, txn_sheet)
        except (OSError, ValueError, KeyError, DatabaseError) as e:
            console_error(f"Error importing {file_path.name}: {e}")
            return FileImport(file_path, error=str(e))

        show_import_summary(file_path.name, results)
        show_import_audit(results, verbose=verbose, interactive=interactive)

    processed_path = get_config().processed_path
    if archive(file_path, processed_path) != file_path:
        console_info(f"Moved {file_path.name} to {processed_path.name}/")
    return FileImport(file_path, results=results)


def confirm_demo_replacement() -> bool:
    """Offer to replace the demo folio before an import joins it.

    Asked only while the folio holds the demo and nothing else; the files are
    not read to find out whose rows they hold. A non-interactive run, with
    nobody to answer, keeps the demo.

    Returns:
        True to go ahead with the import, False to leave everything as it is.
    """
    if not is_demo_folio():
        return True

    console_warning(
        "This folio is the demo folio; importing would mix your transactions into it.",
    )
    try:
        replace = typer.confirm(
            "Replace the demo with your own transactions? A backup is kept.",
            default=False,
        )
    except typer.Abort:
        replace = False
    if replace:
        DemoFolio.clear()
        console_success("Removed the demo folio.")
    return replace


def _cancelled_for_the_demo() -> typer.Exit:
    """Report a declined import over the demo, ready to be raised."""
    console_info("Import cancelled; the demo folio is unchanged.")
    return typer.Exit(1)


def _import_file_and_export(file_path: Path, *, verbose: bool = False) -> None:
    """Import a single file and export to Parquet."""
    if not confirm_demo_replacement():
        raise _cancelled_for_the_demo()
    outcome = import_file(file_path, verbose=verbose)
    if outcome.imported > 0:
        export_to_parquet()
        save_update_baseline()
    else:
        console_warning(f"No transactions imported from {file_path.name}")


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
        interactive: Whether each audit offers to expand its sections by keypress.

    Returns:
        One entry per file, in the order imported. Empty when there were none.
    """
    import_files = waiting_files(dir_path)
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

    show_files_summary(outcomes)
    return outcomes


def _import_directory_and_export(dir_path: Path, *, verbose: bool = False) -> None:
    """Import all files from directory and export to Parquet."""
    if waiting_files(dir_path) and not confirm_demo_replacement():
        raise _cancelled_for_the_demo()
    outcomes = import_directory(dir_path, verbose=verbose)
    if not outcomes:
        console_error(f"No supported files found in {dir_path}")
        raise typer.Exit(1)

    total_imported = sum(outcome.imported for outcome in outcomes)
    if total_imported > 0:
        console_success(f"Total transactions imported: {total_imported}")
        export_to_parquet()
        save_update_baseline()
    else:
        console_warning("No transactions imported")
