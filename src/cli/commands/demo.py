"""Demo command for the folio CLI.

Builds a realistic demo portfolio from real market history.
"""

from __future__ import annotations

import typer

from app import bootstrap
from cli.commands.generate import write_folio
from datagen import ensure_data_exists
from term import console_error, console_info, console_success
from term.progress import ProgressDisplay

app = typer.Typer()


@app.command(name="")
def create_folio() -> None:
    """Create a demo portfolio from real market history.

    Builds two years of transactions across four accounts if the folio holds no
    data yet, so every command has something to show.
    """
    bootstrap.reload_config()

    try:
        with ProgressDisplay.spinner("sea_green3") as progress:
            progress.add_task("Building the demo portfolio...", total=None)
            created = ensure_data_exists()
    except (OSError, ValueError, KeyError) as e:
        console_error(f"Error creating demo portfolio: {e}")
        raise typer.Exit(1) from e

    if not created:
        console_info("Demo folio not created due to existing data.")
        return

    # Written the same way `folio generate` writes it, so the demo shows what a
    # real folio's workbook looks like.
    if write_folio() is None:
        console_error("Error: Failed to create demo portfolio")
        raise typer.Exit(1)
    console_success("Demo portfolio created successfully!")
    console_info("Check your database path for the generated folio database.")


if __name__ == "__main__":
    app()
