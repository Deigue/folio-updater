"""Choose an export format from a path, and write to it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from exporters.csv_writer import write_csv
from exporters.excel_writer import write_workbook

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import pandas as pd

    from exporters.sheet_spec import Sheet

WORKBOOK_SUFFIX = ".xlsx"
CSV_SUFFIX = ".csv"
# The one format that stays a raw dump: it is read by programs, not people.
PARQUET_SUFFIX = ".parquet"

SUPPORTED = (WORKBOOK_SUFFIX, CSV_SUFFIX)


class UnsupportedExportError(ValueError):
    """An export path naming a format this command cannot write."""

    def __init__(self, suffix: str) -> None:
        """Name the suffix that was asked for, and the ones that would work."""
        listed = ", ".join(SUPPORTED)
        super().__init__(f"Cannot export to '{suffix}' files. Use one of: {listed}.")


class SingleSheetError(ValueError):
    """An export asked a format that holds one table to hold several."""

    MESSAGE = (
        "A CSV holds one table. Export to .xlsx for a sheet per grain, or "
        "narrow the request to one grain."
    )
    SEVERAL_TABLES_MESSAGE = (
        "A CSV holds one table, and '{name}' is several on one sheet. Export to .xlsx."
    )

    def __init__(self, sheet_with_tables: str | None = None) -> None:
        """Say why the sheets will not fit in one CSV.

        Args:
            sheet_with_tables: The name of a single sheet holding several tables,
                or None when the problem is several sheets.
        """
        if sheet_with_tables is None:
            super().__init__(self.MESSAGE)
        else:
            super().__init__(
                self.SEVERAL_TABLES_MESSAGE.format(name=sheet_with_tables),
            )


def write_export(path: Path, sheets: Sequence[Sheet]) -> Path:
    """Write one or more sheets to the format the path asks for.

    Args:
        path: Where to write. A missing suffix means a workbook.
        sheets: The sheets, in the order they should appear.

    Returns:
        The path actually written, which gains a suffix when the caller gave
        none.

    Raises:
        UnsupportedExportError: If the suffix names a format that cannot be
            written.
        SingleSheetError: If several sheets, or one holding several tables,
            were asked to share a CSV.
    """
    target = path if path.suffix else path.with_suffix(WORKBOOK_SUFFIX)
    suffix = target.suffix.lower()

    if suffix == CSV_SUFFIX:
        if len(sheets) != 1:
            raise SingleSheetError
        if len(sheets[0].tables) > 1:
            raise SingleSheetError(sheets[0].name)
        write_csv(target, sheets[0])
        return target

    if suffix != WORKBOOK_SUFFIX:
        raise UnsupportedExportError(suffix)

    write_workbook(target, sheets)
    return target


def write_frame_parquet(path: Path, frame: pd.DataFrame) -> None:
    """Write a frame as it is, at full precision, for a reader that is a program.

    Args:
        path: Where to write.
        frame: The rows, with no sheet arrangement applied.
    """
    frame.to_parquet(path, engine="pyarrow", index=False)
