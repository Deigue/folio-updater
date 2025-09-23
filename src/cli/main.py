"""Main CLI application for folio-updater.

This module provides the main CLI interface using Typer.
"""

from __future__ import annotations

import typer

from cli.commands import demo, getfx, import_cmd

app = typer.Typer(
    name="folio",
    help="Folio Updater - Portfolio management CLI tool",
    add_completion=False,
)

app.command("demo", help="Create demo portfolio with mock data")(
    demo.create_folio,
)


def main() -> None:
    """Entry point for the CLI application."""
    app()


if __name__ == "__main__":
    main()
