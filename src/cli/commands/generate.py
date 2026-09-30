"""The `folio generate` command.

Writes the whole folio as one workbook: a summary, a dashboard per pool, the
cost-base ledger, and the stored transactions, rates and tickers it was all
computed from. Everything is read off one valuation, so every sheet agrees with
every other.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer

from app import bootstrap, get_config
from cli.commands.common import (
    FIX_UNCONVERTIBLE,
    ensure_fx_coverage,
    freshness_note,
    replay_result,
)
from db.backup import rolling_backup
from engine.cache import load_or_build
from engine.fx_rates import FxRateUnavailableError
from engine.panels import FolioValuation
from exporters.folio_workbook import (
    SECTIONS,
    FolioSources,
    Section,
    UnknownSectionError,
    folio_tables,
    parse_sections,
)
from exporters.output import SingleSheetError, UnsupportedExportError, write_export
from term import console_error, console_info, console_success, console_warning
from term.progress import ProgressDisplay

if TYPE_CHECKING:
    from collections.abc import Sequence

    from exporters.table import Table

# The currency every dashboard sheet is valued in: each holding in the one it
# trades in, the way a broker shows it. The cost base in CAD has a sheet of its
# own.
_VALUATION = "native"


def write_folio(
    target: Path | None = None,
    sections: Sequence[Section] = SECTIONS,
    *,
    refresh: bool = False,
    offline: bool = False,
) -> Path | None:
    """Value the folio and write it out as a workbook.

    Args:
        target: Where to write, or None for the configured folio path. The
            configured path keeps its rolling backups; any other is simply
            overwritten.
        sections: The groups of sheets to include.
        refresh: Refetch every quote and rebuild the cost-base cache.
        offline: Never touch the network: price from cached quotes only.

    Returns:
        The path written, or None when there was nothing to write.

    Raises:
        typer.Exit: If the target names a format that cannot hold a workbook.
    """
    if not offline:
        # Quotes are today's, so the rate that converts them has to be too.
        ensure_fx_coverage(through_today=True)

    try:
        cached = load_or_build(refresh=refresh)
    except FxRateUnavailableError as error:
        # The stored tables are still worth having: a workbook of the raw
        # transactions is what `generate` wrote before it could value anything.
        console_warning(
            f"{error} The cost base cannot be computed, so only the stored "
            f"transactions, rates and tickers are written. {FIX_UNCONVERTIBLE}",
        )
        if Section.STORED not in sections:
            return None
        return _write(target, folio_tables(FolioSources.read(None), sections))

    if cached.frame.empty:
        console_warning("No transactions to generate a workbook from.")
        return None

    result = replay_result(cached)

    valuation = FolioValuation.build(
        cached.frame,
        result,
        currency=_VALUATION,
        refresh=refresh,
        offline=offline,
    )
    note = freshness_note(cached.computed_at, valuation.quotes)
    tables = folio_tables(FolioSources.read(valuation), sections, notes=[note])
    return _write(target, tables)


def _write(target: Path | None, tables: Sequence[Table]) -> Path:
    """Write the tables out, keeping rolling backups of the configured folio.

    Args:
        target: Where to write, or None for the configured folio path.
        tables: The sheets, in order.

    Returns:
        The path written.

    Raises:
        typer.Exit: If the target names a format that cannot hold a workbook.
    """
    config = get_config()
    path = target or config.folio_path
    with ProgressDisplay.spinner("yellow") as progress:
        progress.add_task("Writing the workbook...", total=None)
        if path == config.folio_path and path.exists():
            rolling_backup(path)
        try:
            written = write_export(path, tables)
        except (UnsupportedExportError, SingleSheetError) as error:
            console_error(str(error))
            raise typer.Exit(1) from error

    console_success(f"Wrote {len(tables)} sheets to {written}")
    console_info(", ".join(table.name for table in tables))
    return written


def generate_excel(
    out: str | None = None,
    only: str | None = None,
    *,
    refresh: bool = False,
    offline: bool = False,
) -> None:
    """Generate the folio workbook.

    Args:
        out: Where to write instead of the configured folio path.
        only: Section names separated by commas, to write only those sheets.
        refresh: Refetch every quote and rebuild the cost-base cache.
        offline: Never touch the network: price from cached quotes only.

    Raises:
        typer.Exit: On an unusable request.
    """
    bootstrap.reload_config()
    try:
        sections = parse_sections(only)
    except UnknownSectionError as error:
        console_error(str(error))
        raise typer.Exit(1) from error
    if not sections:
        console_error("--only named no sections, so there is nothing to write.")
        raise typer.Exit(1)

    write_folio(
        Path(out) if out else None,
        sections,
        refresh=refresh,
        offline=offline,
    )
