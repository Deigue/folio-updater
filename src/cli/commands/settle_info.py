"""Settlement info command for the folio CLI.

Handles querying settlement date information for transactions in the database.
"""

from __future__ import annotations

import sqlite3

import typer

from db.db import get_connection
from utils.constants import Column, Table


def settlement_info() -> None:
    """Show settlement date information for transactions in the database."""
    try:
        with get_connection() as conn:
            # Get total number of transactions with calculated settlement dates
            calculated_query = f"""
            SELECT COUNT(*) as count
            FROM "{Table.TXNS}"
            WHERE "{Column.Txn.SETTLE_CALCULATED}" = 1
            """

            cursor = conn.execute(calculated_query)
            calculated_count = cursor.fetchone()[0]

            # Get total number of transactions
            total_query = f"""
            SELECT COUNT(*) as count
            FROM "{Table.TXNS}"
            """

            cursor = conn.execute(total_query)
            total_count = cursor.fetchone()[0]

            typer.echo("Settlement Date Statistics:")
            typer.echo(f"  Total transactions: {total_count}")
            typer.echo(f"  Calculated settlement dates: {calculated_count}")
            typer.echo(f"  Provided settlement dates: {total_count - calculated_count}")

            if calculated_count > 0:
                typer.echo("\nTransactions with calculated settlement dates:")

                # Get list of transactions with calculated settlement dates
                list_query = f"""
                SELECT
                    "{Column.Txn.TXN_ID}",
                    "{Column.Txn.TXN_DATE}",
                    "{Column.Txn.ACTION}",
                    "{Column.Txn.TICKER}",
                    "{Column.Txn.AMOUNT}",
                    "{Column.Txn.CURRENCY}",
                    "{Column.Txn.SETTLE_DATE}",
                    "{Column.Txn.ACCOUNT}"
                FROM "{Table.TXNS}"
                WHERE "{Column.Txn.SETTLE_CALCULATED}" = 1
                ORDER BY "{Column.Txn.TXN_DATE}" DESC,
                         "{Column.Txn.TXN_ID}" DESC
                """

                cursor = conn.execute(list_query)
                transactions = cursor.fetchall()

                # Header
                typer.echo(
                    f"{'ID':<6} {'TxnDate':<12} {'Action':<12} {'Ticker':<10} "
                    f"{'Amount':<12} {'Curr':<4} {'SettleDate':<12} {'Account':<10}",
                )
                typer.echo("-" * 85)

                # Transaction rows
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
