"""Main CLI application for folio-updater.

This module provides the main CLI interface using Typer.
"""

from __future__ import annotations

import typer

__version__ = "0.3.0"

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
        help=(
            "Directory with files to import. Use 'default' to use default import "
            "directory."
        ),
    ),
) -> None:
    """Import transactions into the folio."""
    from cli.commands.import_cmd import import_transaction_files

    import_transaction_files(file=file, directory=directory)


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
) -> None:
    """Show settlement date information."""
    from cli.commands.settle_info import settlement_info

    settlement_info(file=file)


@app.command("download", help="Download statements from brokers")
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
    token: bool = typer.Option(
        default=False,
        help="Prompt to set/override the flex token for IBKR",
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
        token=token,
        reference_code=reference_code,
    )


@app.command("version")
def show_version() -> None:
    """Show the version and exit."""
    typer.echo(f"folio-updater version: {__version__}")


def main() -> None:
    """Entry point for the CLI application."""
    app()


if __name__ == "__main__":
    main()
