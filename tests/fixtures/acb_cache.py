"""Keep the saved ledger in memory instead of on disk.

`engine.ledger` saves the ledger as two Parquet files plus a JSON meta. Every
command that opens the ledger (`dash`, `acb`, `check`, `quotes`) writes them
whenever the saved ledger is cold, and under test it is cold in every
`temp_ctx`, so the write lands once per test, which is extremely expensive.

Only the file reads and writes are swapped: deciding whether the saved ledger is
current, and laying it out and restoring it, run for real.

Tests that assert on the files themselves carry the `real_acb_cache` marker to opt in.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from engine import ledger as saved_ledger

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    import pandas as pd


class _MemoryFiles:
    """Stand-in for the saved ledger's files, keeping only the latest save."""

    def __init__(self) -> None:
        """Start with nothing saved, the way a fresh `temp_ctx` does."""
        self._entries: dict[str, dict[str, Any]] = {}

    def read_meta(self, parquet: Path) -> dict[str, Any] | None:
        """Return the saved meta, or None when nothing was saved.

        Args:
            parquet: The path the real ledger would have been saved beside,
                used only as the key.

        Returns:
            The meta, parsed back from JSON exactly as the real file would be.
        """
        entry = self._entries.get(str(parquet))
        return None if entry is None else json.loads(entry["meta"])

    def read_frame(self, parquet: Path, save: str) -> pd.DataFrame:
        """Return a copy of the saved ledger frame.

        Only the latest save is kept, and the meta read always names it.

        Args:
            parquet: The path the real ledger would have used, as the key.
            save: The save the meta names.

        Returns:
            A copy, so no caller can change what is saved.
        """
        entry = self._entries[str(parquet)]
        assert entry["save"] == save
        return entry["frame"].copy()

    def read_diagnostics(self, parquet: Path, save: str) -> pd.DataFrame:
        """Return a copy of the saved diagnostics.

        Args:
            parquet: The path the real ledger would have used, as the key.
            save: The save the meta named when the ledger was read.

        Returns:
            A copy, so no caller can change what is saved.
        """
        entry = self._entries[str(parquet)]
        assert entry["save"] == save
        return entry["diagnostics"].copy()

    def write_files(
        self,
        parquet: Path,
        frame: pd.DataFrame,
        diagnostics: pd.DataFrame,
        meta: dict[str, Any],
    ) -> None:
        """Keep the files a save would have written, replacing the last save.

        Args:
            parquet: The path the real ledger would have used, as the key.
            frame: The ledger frame.
            diagnostics: The diagnostics with the rows they tag.
            meta: The inputs, save, computed time, cash and row counts.
        """
        self._entries[str(parquet)] = {
            "save": meta["save"],
            "frame": frame.copy(),
            "diagnostics": diagnostics.copy(),
            # Serialised, so a meta that cannot survive the trip fails here
            # exactly as it would through the real file.
            "meta": json.dumps(meta),
        }

    def clear(self) -> None:
        """Drop everything, so no ledger outlives the test that saved it."""
        self._entries.clear()


@pytest.fixture(autouse=True)
def memory_acb_cache(request: pytest.FixtureRequest) -> Generator[None]:
    """Swap the saved ledger's disk round trip for an in-memory one.

    Args:
        request: Used to honour the `real_acb_cache` marker.

    Yields:
        None, for the duration of the test.
    """
    if request.node.get_closest_marker("real_acb_cache"):
        yield
        return

    files = _MemoryFiles()
    with (
        patch.object(saved_ledger, "_read_meta", side_effect=files.read_meta),
        patch.object(saved_ledger, "_read_frame", side_effect=files.read_frame),
        patch.object(
            saved_ledger,
            "_read_diagnostics",
            side_effect=files.read_diagnostics,
        ),
        patch.object(saved_ledger, "_write_files", side_effect=files.write_files),
    ):
        yield
    files.clear()
