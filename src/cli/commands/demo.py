"""Demo command for the folio CLI.

Builds a realistic demo folio from real market history.
"""

from __future__ import annotations

import typer

from app import bootstrap
from cli.commands.generate import write_folio
from datagen import ensure_data_exists
from term import console_error, console_info, console_success
from term.progress import spinner
from ui.vocabulary import SPINNER_DEMO


def create_folio() -> None:
    """Create a demo folio from real market history.

    Builds two years of transactions across four accounts if the folio holds no
    data yet, so every command has something to show.
    """
    bootstrap.reload_config()

    try:
        with spinner(SPINNER_DEMO) as progress:
            progress.add_task("Building the demo folio...", total=None)
            created = ensure_data_exists()
    except (OSError, ValueError, KeyError) as e:
        console_error(f"Error creating demo folio: {e}")
        raise typer.Exit(1) from e

    if not created:
        console_info("Demo folio not created due to existing data.")
        return

    # Written the same way `folio generate` writes it, so the demo shows what a
    # real folio's workbook looks like.
    if write_folio() is None:
        console_error("Error: Failed to create demo folio")
        raise typer.Exit(1)
    console_success("Demo folio created successfully!")
    console_info("Check your database path for the generated folio database.")
