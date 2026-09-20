"""Choose an export format from a path, and write to it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from exporters.csv_writer import write_csv
from exporters.excel_writer import write_workbook

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from exporters.table import Table

WORKBOOK_SUFFIX = ".xlsx"
CSV_SUFFIX = ".csv"

SUPPORTED = (WORKBOOK_SUFFIX, CSV_SUFFIX)


class UnsupportedExportError(ValueError):
    """An export path naming a format this command cannot write."""

    def __init__(self, suffix: str, supported: Sequence[str] = SUPPORTED) -> None:
        """Name the suffix that was asked for, and the ones that would work."""
        self.suffix = suffix
        listed = ", ".join(supported)
        super().__init__(f"Cannot export to '{suffix}' files. Use one of: {listed}.")


class SingleSheetError(ValueError):
    """A multi-scope export asked for a format that holds one table."""

    MESSAGE = (
        "A CSV holds one table. Export to .xlsx for a sheet per scope, or "
        "narrow the request to one scope."
    )

    def __init__(self) -> None:
        """Carry the one message this error ever has."""
        super().__init__(self.MESSAGE)


def write_export(path: Path, tables: Sequence[Table]) -> Path:
    """Write one or more tables to the format the path asks for.

    Args:
        path: Where to write. A missing suffix means a workbook.
        tables: The tables, in the order their sheets should appear.

    Returns:
        The path actually written, which gains a suffix when the caller gave
        none.

    Raises:
        UnsupportedExportError: If the suffix names a format that cannot be
            written.
        SingleSheetError: If several tables were asked to share a CSV.
    """
    target = path if path.suffix else path.with_suffix(WORKBOOK_SUFFIX)
    suffix = target.suffix.lower()

    if suffix == CSV_SUFFIX:
        if len(tables) != 1:
            raise SingleSheetError
        write_csv(target, tables[0])
        return target

    if suffix != WORKBOOK_SUFFIX:
        raise UnsupportedExportError(suffix)

    write_workbook(target, tables)
    return target
