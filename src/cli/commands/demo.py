"""Demo command for the folio CLI.

Handles creating a demo portfolio with mock data.
"""

from __future__ import annotations

import typer

from app import bootstrap
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
            typer.echo("Demo portfolio created successfully!")
            typer.echo("Check your database path for the generated folio database.")
        else:  # pragma: no cover
            typer.echo("Error: Failed to create demo portfolio", err=True)
            raise typer.Exit(1)

    except (OSError, ValueError, KeyError) as e:
        typer.echo(f"Error creating demo portfolio: {e}", err=True)
        raise typer.Exit(1) from e


if __name__ == "__main__":  # pragma: no cover
    app()
