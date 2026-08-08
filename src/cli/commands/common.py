"""Shared helpers for folio CLI commands that mutate transactions."""

from __future__ import annotations

from cli import ProgressDisplay, console_success, console_warning
from exporters import ParquetExporter


def export_to_parquet() -> None:
    """Export transactions to Parquet so generated folios stay in sync."""
    try:
        with ProgressDisplay.spinner("green") as progress:
            progress.add_task("Exporting to Parquet...", total=None)
            exporter = ParquetExporter()
            exported = exporter.export_transactions()
        console_success(f"Exported {exported} transactions to Parquet")
    except (OSError, ValueError, KeyError) as e:
        console_warning(f"Failed to export to Parquet: {e}")
