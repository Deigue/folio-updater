"""Keep the cost-base cache in memory instead of on disk.

`engine.cache` persists the master frame as Parquet plus a JSON sidecar. Every
command that reads the cost base (`dash`, `acb`, `check`, `quotes`) writes that
pair whenever the cache is cold, and under test the cache is cold in every
`temp_ctx`, so the write lands once per test, which is extremely expensive.

Tests that assert on the files themselves carry the `real_acb_cache` marker to opt in.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from domain import TORONTO_TZ
from engine import cache as acb_cache
from engine.cache import CachedFrame
from engine.frames import index_by_txn_id
from engine.snapshot import decode as decode_replay
from engine.snapshot import encode as encode_replay

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    import pandas as pd

    from engine.types import ReplayResult


class _MemoryCache:
    """Stand-in for the Parquet file and its `.meta.json` sidecar."""

    def __init__(self) -> None:
        """Start with nothing cached, the way a fresh `temp_ctx` does."""
        self._entries: dict[str, dict[str, Any]] = {}

    def read(self, parquet: Path, expected: str) -> CachedFrame | None:
        """Return the cached frame when its fingerprint still matches.

        Args:
            parquet: The path the real cache would have been written to, used
                only as the key.
            expected: The fingerprint the caller requires.

        Returns:
            The cached frame, or None on a miss or a stale fingerprint.
        """
        entry = self._entries.get(str(parquet))
        if entry is None or entry["fingerprint"] != expected:
            return None
        snapshot = entry["replay"]
        return CachedFrame(
            # The real path loses the index to Parquet and restores it here.
            frame=index_by_txn_id(entry["frame"].copy()),
            computed_at=entry["computed_at"],
            result=decode_replay(json.loads(snapshot)) if snapshot else None,
        )

    def write(
        self,
        parquet: Path,
        frame: pd.DataFrame,
        expected: str,
        result: ReplayResult | None = None,
    ) -> None:
        """Store a freshly built frame under its fingerprint.

        Args:
            parquet: The path the real cache would have used, as the key.
            frame: The master frame to keep.
            expected: The fingerprint the frame was built at.
            result: The replay behind the frame, snapshotted the way the real
                cache snapshots it.
        """
        snapshot = encode_replay(result) if result is not None else None
        self._entries[str(parquet)] = {
            "fingerprint": expected,
            "computed_at": datetime.now(TORONTO_TZ),
            # Serialised and parsed back, so a snapshot that cannot survive the
            # trip fails here exactly as it would through the sidecar.
            "replay": json.dumps(snapshot) if snapshot is not None else None,
            "frame": frame.copy(),
        }

    def clear(self) -> None:
        """Drop everything, so no frame outlives the test that built it."""
        self._entries.clear()


@pytest.fixture(autouse=True)
def memory_acb_cache(request: pytest.FixtureRequest) -> Generator[None]:
    """Swap the cost-base cache's disk round trip for an in-memory one.

    Args:
        request: Used to honour the `real_acb_cache` marker.

    Yields:
        None, for the duration of the test.
    """
    if request.node.get_closest_marker("real_acb_cache"):
        yield
        return

    store = _MemoryCache()
    with (
        patch.object(acb_cache, "_read_cache", side_effect=store.read),
        patch.object(acb_cache, "_write_cache", side_effect=store.write),
    ):
        yield
    store.clear()
