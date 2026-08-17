"""Cache the master frame so repeated cost-base commands stay instant.

Invalidation is automatic: `add`, `edit`, `delete` and `import`
all move the transactions fingerprint, and a change to the config keys that
affect the arithmetic moves the config hash.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from app import get_config
from db.queries import get_connection, get_txns_fingerprint
from engine.events import load_txn_rows
from engine.frames import index_by_txn_id, master_frame
from engine.fx_rates import load_fx_rates
from engine.replay import ReplayConfig, replay
from services.symbols import load_symbol_resolver
from utils.constants import TORONTO_TZ

if TYPE_CHECKING:
    from pathlib import Path

    from engine.events import ReplayResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CachedFrame:
    """A master frame together with how it was obtained.

    Attributes:
        frame: The master frame. (cached or computed)
        computed_at: When the frame was built. `None` means it was built by this
            invocation.
        result: The replay behind the frame, present only on a fresh build.
            Warnings live here, so a cache hit deliberately has none to show.
    """

    frame: pd.DataFrame
    computed_at: datetime | None = None
    result: ReplayResult | None = None

    @property
    def from_cache(self) -> bool:
        """Whether this frame was read from disk rather than computed now."""
        return self.computed_at is not None


def _meta_path(parquet: Path) -> Path:
    """Where the fingerprint for a cached frame is stored."""
    return parquet.with_suffix(".meta.json")


def _config_hash() -> str:
    """Hash the config keys that change the arithmetic.

    Only `accounts.map` and `accounts.defaults` qualify: they decide an
    account's tax type and where its commissions sit.
    """
    config = get_config()
    payload = json.dumps(
        {
            "map": {str(k): str(v) for k, v in sorted(config.account_types.items())},
            "defaults": str(config.account_fee_default),
            "naming": config.account_naming_convention,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def fingerprint() -> str:
    """Summarise the inputs a replay depends on.

    Returns:
        A digest of the transactions table and the config keys that affect the
        arithmetic. Any change to either produces a different string.
    """
    with get_connection() as conn:
        txns = get_txns_fingerprint(conn)
    return f"{txns}#{_config_hash()}"


def build() -> CachedFrame:
    """Run a replay and build the master frame from it.

    Returns:
        A freshly computed `CachedFrame`, carrying the replay result.
    """
    rows = load_txn_rows()
    resolver = load_symbol_resolver()
    result = replay(rows, load_fx_rates(), ReplayConfig.build(rows, resolver))
    return CachedFrame(frame=master_frame(result), result=result)


def _read_cache(parquet: Path, expected: str) -> CachedFrame | None:
    """Read the cached frame when its fingerprint still matches."""
    meta_path = _meta_path(parquet)
    if not parquet.exists() or not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.debug("Unreadable cost-base cache metadata; rebuilding")
        return None
    if meta.get("fingerprint") != expected:
        logger.debug("Cost-base cache is stale; rebuilding")
        return None
    try:
        frame = pd.read_parquet(parquet, engine="fastparquet")
    except (OSError, ValueError):
        logger.debug("Unreadable cost-base cache; rebuilding")
        return None
    computed_at = datetime.fromisoformat(meta["computed_at"])
    # Parquet stores no index, so restore the one the frame was built with.
    return CachedFrame(frame=index_by_txn_id(frame), computed_at=computed_at)


def _write_cache(parquet: Path, frame: pd.DataFrame, expected: str) -> None:
    """Persist a freshly built frame alongside its fingerprint."""
    try:
        frame.to_parquet(parquet, engine="fastparquet", index=False)
        _meta_path(parquet).write_text(
            json.dumps(
                {
                    "fingerprint": expected,
                    "computed_at": datetime.now(TORONTO_TZ).isoformat(),
                },
            ),
            encoding="utf-8",
        )
    except (OSError, ValueError):
        # A cache that cannot be written is a performance problem, never a
        # correctness one: the caller already holds the computed frame.
        logger.warning("Could not write the cost-base cache to %s", parquet)


def load_or_build(*, refresh: bool = False) -> CachedFrame:
    """Return the master frame, from cache when it is still valid.

    Args:
        refresh: Rebuild and rewrite the cache even if it looks current.

    Returns:
        The master frame, and when it was computed. A `computed_at` of `None`
        means this invocation built it.
    """
    parquet = get_config().acb_parquet
    expected = fingerprint()

    if not refresh:
        cached: CachedFrame | None = _read_cache(parquet, expected)
        if cached is not None:
            return cached

    built: CachedFrame = build()
    _write_cache(parquet, built.frame, expected)
    return built
