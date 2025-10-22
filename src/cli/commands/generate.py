"""Generate command for the folio CLI.

Generates Excel workbook from Parquet storage files.
"""

from __future__ import annotations

import typer

from app import bootstrap
from exporters.excel_exporter import ExcelExporter

app = typer.Typer()


@app.command(name="")
def generate_excel() -> None:
    """Generate Excel workbook from Parquet storage files.

    Reads various parquet files from the configured data_path and combines them into a
    single Excel workbook at folio_path.
    """
    bootstrap.reload_config()
    exporter = ExcelExporter()
    success = exporter.generate_excel()

    if success:
        typer.echo("Excel workbook generated successfully!")
    else:
        typer.echo("Error: Failed to generate Excel workbook", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
