"""Generate command for the folio CLI.

Generates Excel workbook from Parquet storage files.
"""

from __future__ import annotations

import typer

from app import bootstrap
from cli import console_error, console_success
from cli.display import ProgressDisplay
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
    with ProgressDisplay.spinner("yellow") as progress:
        progress.add_task("Generating Excel workbook...", total=None)
        success = exporter.generate_excel()

    if success:
        console_success("Excel workbook generated successfully!")
    else:
        console_error("Failed to generate Excel workbook")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
