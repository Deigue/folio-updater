"""Main CLI application for folio-updater.

This module provides the main CLI interface using Typer.
"""

from __future__ import annotations

import typer

__version__ = "0.1.0"

app = typer.Typer(
    name="folio",
    help="Folio Updater - Portfolio management CLI tool",
    add_completion=False,
)


@app.command("import", help="Import transactions from files")
def import_transactions_cmd(
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
def settle_info_cmd() -> None:
    """Show settlement date information."""
    from cli.commands.settle_info import settlement_info

    settlement_info()


@app.command("version")
def show_version() -> None:
    """Show the version and exit."""
    typer.echo(f"folio-updater version: {__version__}")


def main() -> None:  # pragma: no cover
    """Entry point for the CLI application."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
