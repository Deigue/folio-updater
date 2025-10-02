"""Main CLI application for folio-updater.

This module provides the main CLI interface using Typer.
"""

from __future__ import annotations

import typer

from cli.commands import demo, getfx, import_cmd, settle_info

__version__ = "0.1.0"

app = typer.Typer(
    name="folio",
    help="Folio Updater - Portfolio management CLI tool",
    add_completion=False,
)

app.command("import", help="Import transactions from files")(
    import_cmd.import_transaction_files,
)
app.command("getfx", help="Update foreign exchange rates")(
    getfx.update_fx_rates,
)
app.command("demo", help="Create demo portfolio with mock data")(
    demo.create_folio,
)
app.command("settle-info", help="Show settlement date information")(
    settle_info.settlement_info,
)


@app.command("version")
def show_version() -> None:
    """Show the version and exit."""
    typer.echo(f"folio-updater version: {__version__}")


def main() -> None:  # pragma: no cover
    """Entry point for the CLI application."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
