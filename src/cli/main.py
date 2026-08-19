"""Main CLI application for folio-updater.

This module provides the main CLI interface using Typer.
"""

from __future__ import annotations

import sys
from importlib.metadata import version as _pkg_version
from pathlib import Path

import typer

from app import bootstrap
from ui import console_info, console_print
from utils.config import Config

__version__ = _pkg_version("folio-updater")

app = typer.Typer(
    name="folio",
    help="Folio Updater - Portfolio management CLI tool",
    add_completion=False,
    no_args_is_help=True,
)


@app.command("import", help="Import transactions from files")
def import_transactions_cmd(
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
        help=("Directory with files to import."),
    ),
    *,
    verbose: bool = typer.Option(
        False,
        "-v",
        "--verbose",
        help="Display the final imported transactions",
    ),
) -> None:
    """Import transactions into the folio."""
    from cli.commands.import_cmd import import_transaction_files

    import_transaction_files(file=file, directory=directory, verbose=verbose)


@app.command("add", help="Add a single transaction to the folio")
def add_cmd(  # noqa: PLR0917
    action: str | None = typer.Option(
        None,
        "-a",
        "--action",
        help="Transaction action (BUY, SELL, SPLIT, ROC, DIVIDEND, ...)",
    ),
    date: str | None = typer.Option(
        None,
        "-d",
        "--date",
        help="Transaction date in YYYY-MM-DD format (default: today)",
    ),
    account: str | None = typer.Option(
        None,
        "-n",
        "--account",
        help="Account alias the transaction belongs to",
    ),
    currency: str | None = typer.Option(
        None,
        "-c",
        "--currency",
        help="Transaction currency (USD, CAD, EUR)",
    ),
    ticker: str | None = typer.Option(None, "-t", "--ticker", help="Security ticker"),
    amount: str | None = typer.Option(
        None,
        "-m",
        "--amount",
        help="Total transaction amount",
    ),
    price: str | None = typer.Option(
        None,
        "-p",
        "--price",
        help="Price per unit (shares BEFORE the split for SPLIT)",
    ),
    units: str | None = typer.Option(
        None,
        "-u",
        "--units",
        help="Number of units (shares AFTER the split for SPLIT)",
    ),
    fee: str | None = typer.Option(None, "--fee", help="Transaction fee"),
    set_values: list[str] | None = typer.Option(
        None,
        "--set",
        help="KEY=VALUE for optional columns, repeatable (e.g. --set Description=RSU)",
    ),
    *,
    force: bool = typer.Option(
        False,
        "--force",
        help="Add even if it duplicates an existing transaction",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Validate and preview the transaction without writing it",
    ),
) -> None:
    """Add a single transaction to the folio."""
    from cli.commands.add import add_transaction

    add_transaction(
        action=action,
        date=date,
        account=account,
        currency=currency,
        ticker=ticker,
        amount=amount,
        price=price,
        units=units,
        fee=fee,
        set_values=set_values,
        force=force,
        dry_run=dry_run,
    )


@app.command("getfx", help="Update foreign exchange rates")
def getfx_cmd() -> None:
    """Update foreign exchange rates."""
    from cli.commands.getfx import update_fx_rates

    update_fx_rates()


@app.command("generate", help="Generate the portfolio with latest data")
def generate_cmd() -> None:
    """Generate the portfolio with latest data."""
    from cli.commands.generate import generate_excel

    generate_excel()


@app.command("demo", help="Create demo portfolio with mock data")
def demo_cmd() -> None:
    """Create demo portfolio with mock data."""
    from cli.commands.demo import create_folio

    create_folio()


@app.command("settle-info", help="Show settlement date information")
def settle_info_cmd(
    file: str | None = typer.Option(
        None,
        "-f",
        "--file",
        help="Monthly statement file to import for settlement date updates",
    ),
    *,
    import_flag: bool = typer.Option(
        False,
        "-i",
        "--import",
        help="Import statement files to update settlement dates",
    ),
) -> None:
    """Show settlement date information."""
    from cli.commands.settle_info import settlement_info

    settlement_info(file=file, import_flag=import_flag)


@app.command("download", help="Download transactions from brokers")
def download_cmd(
    broker: str = typer.Option(
        "ibkr",
        "-b",
        "--broker",
        help="Broker to download from (default: 'ibkr')",
    ),
    from_date: str | None = typer.Option(
        None,
        "-f",
        "--from",
        help="Date in YYYY-MM-DD format (default: latest transaction from broker)",
    ),
    to_date: str | None = typer.Option(
        None,
        "-t",
        "--to",
        help="Date in YYYY-MM-DD format (default: today)",
    ),
    *,
    credentials: bool = typer.Option(
        default=False,
        help="Reset credentials for the broker",
    ),
    statement: bool = typer.Option(
        default=False,
        help="Download monthly statement using from date (Wealthsimple only)",
    ),
    reference_code: str | None = typer.Option(
        None,
        "-r",
        "--reference",
        help="Reference code to retry download for IBKR",
    ),
) -> None:
    """Download transactions from broker and save as CSV files."""
    from cli.commands.download import download_statements

    download_statements(
        broker=broker,
        from_date=from_date,
        to_date=to_date,
        credentials=credentials,
        statement=statement,
        reference_code=reference_code,
    )


@app.command("symbol", help="Manage ticker aliases")
def symbol_cmd(
    add: tuple[str, str, str] | None = typer.Option(
        None,
        "-a",
        "--add",
        help="Add an alias. Usage: --add <OLD_TICKER> <NEW_TICKER> <YYYY-MM-DD>",
    ),
    delete: str = typer.Option(
        None,
        "-d",
        "--delete",
        help="Delete an alias by specifying the OLD_TICKER.",
    ),
    *,
    list_all: bool = typer.Option(
        False,
        "-l",
        "--list",
        help="List all aliases.",
    ),
) -> None:
    """Manage ticker aliases for tracking symbol changes over time."""
    from cli.commands.symbol import manage_symbols

    manage_symbols(add, delete, list_all=list_all)


@app.command("acb", help="Show the adjusted cost base buildup for a symbol")
def acb_cmd(  # noqa: PLR0917
    symbol: str | None = typer.Argument(
        None,
        help="Security to report on. Required unless --summary or --export.",
    ),
    account_type: str | None = typer.Option(
        None,
        "-t",
        "--type",
        help="Pool by account type: nreg (default), tfsa, rrsp, ...",
    ),
    account: str | None = typer.Option(
        None,
        "-a",
        "--account",
        help="Report a single broker account instead of a pooled type",
    ),
    currency: str = typer.Option(
        "both",
        "--currency",
        help="CAD, USD, or both (default: both for USD holdings)",
    ),
    date_from: str | None = typer.Option(
        None,
        "--from",
        help="Only transactions on or after this date (YYYY-MM-DD)",
    ),
    date_to: str | None = typer.Option(
        None,
        "--to",
        help="Only transactions on or before this date (YYYY-MM-DD)",
    ),
    year: int | None = typer.Option(
        None,
        "--year",
        help="Shorthand for --from YYYY-01-01 --to YYYY-12-31",
    ),
    export: str | None = typer.Option(
        None,
        "--export",
        help="Write the reported rows to a .csv or .parquet file",
    ),
    *,
    folio: bool = typer.Option(
        False,
        "--folio",
        help="Report the portfolio-wide pool",
    ),
    show_all: bool = typer.Option(
        False,
        "--all",
        help="Include DIVIDEND and FCH rows",
    ),
    summary: bool = typer.Option(
        False,
        "--summary",
        help="One row per symbol instead of a per-transaction buildup",
    ),
    refresh: bool = typer.Option(
        False,
        "--refresh",
        help="Rebuild the cached cost-base frame",
    ),
) -> None:
    """Show the adjusted cost base buildup for a symbol."""
    from cli.commands.acb import show_acb

    show_acb(
        symbol=symbol,
        account_type=account_type,
        account=account,
        currency=currency,
        date_from=date_from,
        date_to=date_to,
        year=year,
        export=export,
        folio=folio,
        show_all=show_all,
        summary=summary,
        refresh=refresh,
    )


@app.command("check", help="Check the folio for missing or inconsistent transactions")
def check_cmd(
    only: str | None = typer.Option(
        None,
        "--only",
        help="Report only one check, e.g. --only unit-balances",
    ),
    *,
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable results instead of the report",
    ),
) -> None:
    """Check the folio for missing or inconsistent transactions."""
    from cli.commands.check import run_folio_checks

    run_folio_checks(only=only, as_json=as_json)


@app.command(
    "query",
    help="Query transactions from the database",
    no_args_is_help=True,
)
def query_cmd(
    terms: list[str] = typer.Argument(
        ...,
        help="Query terms to filter transactions.",
    ),
) -> None:
    """Query transactions from the database."""
    from cli.commands.query import query_transactions

    query_transactions(terms)


@app.command("edit", help="Edit transactions in the folio", no_args_is_help=True)
def edit_cmd(
    selection: list[str] = typer.Argument(
        ...,
        help="TxnIds, or query terms using the `folio query` syntax.",
    ),
    set_values: list[str] = typer.Option(
        ...,
        "--set",
        help=(
            "Field=VALUE, or Field*=N / Field/=N / Field+=N / Field-=N to "
            "compute from the current value. Repeatable."
        ),
    ),
    *,
    force: bool = typer.Option(
        False,
        "--force",
        help="Apply without confirming, and allow duplicates",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview the before/after without writing",
    ),
) -> None:
    """Edit transactions in the folio."""
    from cli.commands.edit import edit_transactions

    edit_transactions(selection, set_values, force=force, dry_run=dry_run)


@app.command(
    "delete",
    help="Delete transactions from the folio",
    no_args_is_help=True,
)
def delete_cmd(
    selection: list[str] = typer.Argument(
        ...,
        help="TxnIds, or query terms using the `folio query` syntax.",
    ),
    *,
    force: bool = typer.Option(
        False,
        "--force",
        help="Delete without asking for confirmation",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Preview the matched transactions without deleting them",
    ),
) -> None:
    """Delete transactions from the folio."""
    from cli.commands.delete import delete_transactions

    delete_transactions(selection, force=force, dry_run=dry_run)


@app.command("version")
def show_version() -> None:
    """Show the version and exit."""
    config = bootstrap.reload_config()
    if getattr(sys, "frozen", False):
        app_path = Path(sys.executable).resolve()
    else:
        app_path = Config.get_default_root_directory()

    console_print(f"folio-updater version: {__version__}")
    console_info(f"application path: {app_path}")
    console_info(f"config path: {config.config_path}")
    console_info(f"folio path: {config.folio_path}")
    console_info(f"data path: {config.data_path}")
    console_info(f"backup path: {config.backup_path}")


def main() -> None:
    """Entry point for the CLI application."""
    app()


if __name__ == "__main__":
    main()
