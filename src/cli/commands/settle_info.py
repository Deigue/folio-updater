"""Settlement info command for the folio CLI.

Handles querying settlement date information for transactions in the database.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer

from app import get_config
from cli.commands.common import read_statements, refuse_unsupported_file
from datagen import is_demo_folio
from db import DatabaseError, txns
from engine.update_review import review_remaining
from services.broker_files import SUPPORTED_SUFFIXES, waiting_files
from term import console_error, console_info, console_rule, console_warning
from ui.transaction_table import page_transactions
from ui.views.imports import show_statements_summary
from ui.views.update import show_concerns
from ui.vocabulary import RULE_STATEMENT, TransactionContext
from ui.widgets import show_stats_panel

if TYPE_CHECKING:
    from models import StatementsRead


def settlement_info(
    file: str | None = None,
    *,
    import_flag: bool = False,
    verbose: bool = False,
) -> None:
    """Show settlement date information for transactions in the database.

    Args:
        file: Optional path to monthly statement file to confirm settlement dates from
        import_flag: Whether to import statement files to confirm settlement dates
        verbose: Whether to list each statement row and how it was resolved

    Raises:
        typer.Exit: On an unusable request.
    """
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
    if is_demo_folio():
        console_error(
            "This folio is the demo folio, which no statement belongs to. "
            "Import your own transactions first (folio import offers to replace "
            "the demo), then run folio settle-info -i.",
        )
        raise typer.Exit(1)
    if file:
        statement_path = Path(file)
        if not statement_path.exists():
            console_error(f'Statement file "{file}" does not exist.')
            raise typer.Exit(1)
        refuse_unsupported_file(statement_path)
        statements = read_statements([statement_path], verbose=verbose)
    else:
        statements = _import_statements_from_directory(verbose=verbose)

    concerns, notes = review_remaining(statements)
    if concerns:
        show_concerns(concerns)
    for note in notes:
        console_info(note)


def _import_statements_from_directory(*, verbose: bool = False) -> StatementsRead:
    """Import all statement files from the statements directory.

    Args:
        verbose: Whether to list every statement row and how it was resolved.

    Returns:
        What the statements found did.
    """
    config = get_config()
    statements_dir = config.statements_path

    if not statements_dir.exists():
        console_error(f'Statements directory "{statements_dir}" does not exist.')
        raise typer.Exit(1)

    statement_files = waiting_files(statements_dir)

    if not statement_files:
        console_error(
            f"No statement files ({', '.join(SUPPORTED_SUFFIXES)}) found in "
            f'"{statements_dir}".',
        )
        raise typer.Exit(1)

    console_info(
        f'Found {len(statement_files)} statement file(s) in "{statements_dir}"',
    )

    statements = read_statements(statement_files, verbose=verbose)
    show_statements_summary(statements.read)
    results = statements.read

    total_confirmed = sum(r.settlements_confirmed for r in results)
    total_transfers = sum(r.transfers_created() for r in results)
    console_info(
        f"Confirmed {total_confirmed} date(s) and created "
        f"{total_transfers} transfer(s) "
        f"across {len(statement_files)} file(s).",
    )
    return statements


def _display_settlement_statistics(*, import_flag: bool = False) -> None:
    """Display settlement date statistics for transactions."""
    if import_flag:
        console_rule(style=RULE_STATEMENT)

    try:
        calculated_count = txns.count_calculated()
        total_count = txns.count()

        stats: dict[str, int | str] = {
            "Total transactions": total_count,
            "Calculated settlement dates": calculated_count,
            "Provided settlement dates": total_count - calculated_count,
        }
        show_stats_panel(stats)

        if calculated_count > 0:
            newest_first = sorted(
                txns.calculated_settlements(),
                key=lambda txn: (txn.txn_date, txn.txn_id),
                reverse=True,
            )
            page_transactions(
                newest_first,
                "Transactions with Calculated Settlement Dates",
                30,
                TransactionContext.SETTLEMENT,
            )
        else:
            console_warning(
                "No transactions found with calculated settlement dates.",
            )

    except DatabaseError as e:
        console_error(f"Database error querying settlement info: {e}")
