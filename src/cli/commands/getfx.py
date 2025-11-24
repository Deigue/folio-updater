"""GetFX command for the folio CLI.

Handles updating foreign exchange rates.
"""

from __future__ import annotations

import typer

from app import bootstrap
from cli import ProgressDisplay, console_error, console_success, console_warning
from cli.console import progress_console_context
from exporters.parquet_exporter import ParquetExporter

app = typer.Typer()


@app.command(name="")
def update_fx_rates() -> None:
    """Update foreign exchange rates in the folio."""
    bootstrap.reload_config()

    try:
        exporter = ParquetExporter()
        with ProgressDisplay.spinner_progress("cyan") as progress:
            task = progress.add_task("Updating FX rates...", start=False)
            progress.start_task(task)
            with progress_console_context(progress.console):
                result = exporter.export_forex()

        if result > 0:
            console_success(f"Successfully updated {result} FX rates")
        else:
            console_warning("No new FX rates to update")

    except (OSError, ValueError, KeyError) as e:
        console_error(f"Error updating FX rates: {e}")
        raise typer.Exit(1) from e


if __name__ == "__main__":
    app()
