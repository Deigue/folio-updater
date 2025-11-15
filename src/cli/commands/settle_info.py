"""Settlement info command for the folio CLI.

Handles querying settlement date information for transactions in the database.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import typer

from app import bootstrap
from app.app_context import get_config
from db import db
from db.db import get_connection, get_row_count
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
        typer.echo(
            "ERROR: The --file/-f option only works with --import enabled.",
            err=True,
        )
        raise typer.Exit(1)

    if import_flag:
        _handle_statement_import(file)

    _display_settlement_statistics()


def _handle_statement_import(file: str | None) -> None:
    """Handle statement import based on file parameter."""
    if file:
        statement_path = Path(file)
        if not statement_path.exists():
            typer.echo(f"ERROR: Statement file '{file}' does not exist.", err=True)
            raise typer.Exit(1)
        _import_single_statement(statement_path)
    else:
        _import_statements_from_directory()
    typer.echo()


def _import_single_statement(statement_path: Path) -> int:
    """Import a single statement file and return number of updates."""
    typer.echo(f"IMPORTING settlement dates from: {statement_path}")
    updates = import_statements(statement_path)
    if updates > 0:
        typer.echo(f"SUCCESS: Updated {updates} settlement dates.")
    else:
        typer.echo("No settlement dates were updated.")
    return updates


def _import_statements_from_directory() -> int:
    """Import all statement files from the statements directory."""
    config = get_config()
    statements_dir = config.statements_path

    if not statements_dir.exists():
        typer.echo(
            f"ERROR: Statements directory '{statements_dir}' does not exist.",
            err=True,
        )
        raise typer.Exit(1)

    xlsx_files = list(statements_dir.glob("*.xlsx"))
    csv_files = list(statements_dir.glob("*.csv"))
    statement_files = xlsx_files + csv_files

    if not statement_files:
        typer.echo(
            f"No statement files (.xlsx or .csv) found in '{statements_dir}'.",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(
        f"FOUND {len(statement_files)} statement file(s) in '{statements_dir}'",
    )

    total_updates = 0
    for statement_file in statement_files:
        typer.echo(f"IMPORTING settlement dates from: {statement_file.name}")
        updates = import_statements(statement_file)
        total_updates += updates
        if updates > 0:
            typer.echo(f"  ✓ Updated {updates} settlement dates.")
        else:
            typer.echo("  - No settlement dates updated.")

    typer.echo(
        f"\nTOTAL: Updated {total_updates} settlement dates across "
        f"{len(statement_files)} files.",
    )
    return total_updates


def _display_settlement_statistics() -> None:
    """Display settlement date statistics for all transactions."""
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

            typer.echo("Settlement Date Statistics:")
            typer.echo(f"  Total transactions: {total_count}")
            typer.echo(f"  Calculated settlement dates: {calculated_count}")
            typer.echo(f"  Provided settlement dates: {total_count - calculated_count}")

            if calculated_count > 0:
                typer.echo("\nTransactions with calculated settlement dates:")
                df = db.get_rows(
                    conn,
                    Table.TXNS,
                    condition=f'"{Column.Txn.SETTLE_CALCULATED}" = 1',
                    order_by=(
                        f'"{Column.Txn.TXN_DATE}" DESC, "{Column.Txn.TXN_ID}" DESC'
                    ),
                )

                columns = [
                    Column.Txn.TXN_ID,
                    Column.Txn.TXN_DATE,
                    Column.Txn.ACTION,
                    Column.Txn.TICKER,
                    Column.Txn.AMOUNT,
                    Column.Txn.CURRENCY,
                    Column.Txn.SETTLE_DATE,
                    Column.Txn.ACCOUNT,
                ]
                transactions = (
                    df[columns].itertuples(index=False, name=None)
                    if not df.empty
                    else []
                )

                # Header
                typer.echo(
                    f"{'ID':<6} {'TxnDate':<12} {'Action':<12} {'Ticker':<10} "
                    f"{'Amount':<12} {'Curr':<4} {'SettleDate':<12} {'Account':<10}",
                )
                typer.echo("-" * 85)

                for txn in transactions:
                    (
                        txn_id,
                        txn_date,
                        action,
                        ticker,
                        amount,
                        currency,
                        settle_date,
                        account,
                    ) = txn
                    ticker_str = ticker or ""
                    amount_str = f"{float(amount):,.2f}" if amount else "0.00"

                    typer.echo(
                        f"{txn_id:<6} {txn_date:<12} {action:<12} {ticker_str:<10} "
                        f"{amount_str:<12} {currency:<4} "
                        f"{settle_date:<12} {account:<10}",
                    )
            else:
                typer.echo("\nNo transactions found with calculated settlement dates.")
    except sqlite3.DatabaseError as e:
        typer.echo("Database error querying settlement info:", err=True)
        typer.echo(str(e), err=True)
