"""GetFX command for the folio CLI.

Handles updating foreign exchange rates.
"""

from __future__ import annotations

import typer

from app import bootstrap
from exporters.parquet_exporter import ParquetExporter

app = typer.Typer()


@app.command(name="")
def update_fx_rates() -> None:
    """Update foreign exchange rates in the folio."""
    bootstrap.reload_config()

    try:
        exporter = ParquetExporter()
        result = exporter.export_forex()

        if result > 0:
            typer.echo(f"Successfully updated {result} FX rates")
        else:  # pragma: no cover
            typer.echo("No new FX rates to update")

    except (OSError, ValueError, KeyError) as e:
        typer.echo(f"Error updating FX rates: {e}", err=True)
        raise typer.Exit(1) from e


if __name__ == "__main__":  # pragma: no cover
    app()
