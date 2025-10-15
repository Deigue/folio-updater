"""Settlement info command for the folio CLI.

Handles querying settlement date information for transactions in the database.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import typer

from app import bootstrap
from db import db
from db.db import get_connection, get_row_count
from importers.excel_importer import import_statements
from utils.constants import Column, Table


def settlement_info(file: str | None = None) -> None:
    """Show settlement date information for transactions in the database.

    Args:
        file: Optional path to monthly statement file to import for settlement updates
    """
    bootstrap.reload_config()
    if file:
        statement_path = Path(file)
        if not statement_path.exists():
            typer.echo(f"ERROR: Statement file '{file}' does not exist.", err=True)
            raise typer.Exit(1)

        typer.echo(f"Importing settlement dates from: {statement_path}")
        updates = import_statements(statement_path)
        if updates > 0:
            typer.echo(f"Successfully updated {updates} settlement dates.")
        else:  # pragma: no cover
            typer.echo("No settlement dates were updated.")
        typer.echo()  # Add spacing

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
            else:  # pragma: no cover
                typer.echo("\nNo transactions found with calculated settlement dates.")
    except sqlite3.DatabaseError as e:
        typer.echo("Database error querying settlement info:", err=True)
        typer.echo(str(e), err=True)
