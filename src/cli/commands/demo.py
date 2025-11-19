"""Demo command for the folio CLI.

Handles creating a demo portfolio with mock data.
"""

from __future__ import annotations

import typer

from app import bootstrap
from cli.display import print_error, print_info, print_success
from exporters.excel_exporter import ExcelExporter
from mock.folio_setup import ensure_data_exists

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
            print_success("Demo portfolio created successfully!")
            print_info("Check your database path for the generated folio database.")
        else:
            print_error("Error: Failed to create demo portfolio")
            raise typer.Exit(1)

    except (OSError, ValueError, KeyError) as e:
        print_error(f"Error creating demo portfolio: {e}")
        raise typer.Exit(1) from e


if __name__ == "__main__":
    app()
