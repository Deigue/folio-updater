"""Demo command for the folio CLI.

Handles creating a demo portfolio with mock data.
"""

from __future__ import annotations

import typer

from app import bootstrap
from cli import console_error, console_info, console_success
from datagen import ensure_data_exists
from exporters.excel_exporter import ExcelExporter

app = typer.Typer()


@app.command(name="")
def create_folio() -> None:
    """Create a demo portfolio with mock data.

    This command creates a demo folio with sample data if one doesn't already exist.
    Useful for testing and demonstration.
    """
    bootstrap.reload_config()

    try:
        ensure_data_exists(mock=True)
        exporter = ExcelExporter()
        success = exporter.generate_excel()

        if success:
            console_success("Demo portfolio created successfully!")
            console_info("Check your database path for the generated folio database.")
        else:
            console_error("Error: Failed to create demo portfolio")
            raise typer.Exit(1)

    except (OSError, ValueError, KeyError) as e:
        console_error(f"Error creating demo portfolio: {e}")
        raise typer.Exit(1) from e


if __name__ == "__main__":
    app()
