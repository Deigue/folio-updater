"""Cache the ledger so repeated cost-base commands stay instant.

The saved ledger is reused only when every input the replay reads is unchanged:

- the transactions and ticker aliases, through the change tokens the database
  moves on every write to them;
- the FX rates a conversion could use, summarised up to the latest date the
  folio converts at, saved with the ledger;
- the whole `accounts` section of the config;
- the format the ledger and its snapshot were saved in.
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
from db.queries import get_connection, get_fx_summary, get_revisions
from db.schema import ensure_change_tokens
from domain import TORONTO_TZ
from engine.frames import index_by_txn_id, ledger_frame
from engine.fx_rates import load_fx_rates
from engine.replay import ReplayConfig, replay
from engine.snapshot import SCHEMA_VERSION
from engine.snapshot import decode as decode_replay
from engine.snapshot import encode as encode_replay
from engine.types import load_txn_rows
from services.symbols import load_symbol_resolver
from term import announce

if TYPE_CHECKING:
    from pathlib import Path

    from engine.types import ReplayResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CachedFrame:
    """A ledger together with how it was obtained.

    Attributes:
        frame: The ledger. (cached or computed)
        computed_at: When the frame was built. `None` means it was built by this
            invocation.
        result: The replay behind the frame. A fresh build carries it whole; a
            cache hit rebuilds it using the snapshot stored beside the frame,
            containing the information to rebuild diagnostics.
    """

    frame: pd.DataFrame
    computed_at: datetime | None = None
    result: ReplayResult | None = None

    @property
    def from_cache(self) -> bool:
        """Whether this frame was read from disk rather than computed now."""
        return self.computed_at is not None


def _meta_path(parquet: Path) -> Path:
    """Where a cached frame's inputs and replay snapshot are stored."""
    return parquet.with_suffix(".meta.json")


def _accounts_hash() -> str:
    """Hash the `accounts` config section, which decides types and fee placement."""
    payload = json.dumps(get_config().account_settings, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _inputs() -> dict[str, object]:
    """Describe every replay input but the FX rates."""
    with get_connection() as conn:
        tokens = get_revisions(conn)
    return {"format": SCHEMA_VERSION, "tokens": tokens, "accounts": _accounts_hash()}


def _fx_inputs(cutoff: str | None) -> dict[str, object]:
    """Describe the FX rates a replay converting up to `cutoff` can use."""
    with get_connection() as conn:
        summary = get_fx_summary(conn, cutoff)
    return {"fx_cutoff": cutoff, "fx": summary}


def _is_current(saved: object) -> bool:
    """Whether a saved ledger's inputs still describe the folio.

    An unchanged transactions token means an unchanged cutoff, so checking the
    rates at the saved cutoff is exact. A meta from before these inputs were
    saved is never current.
    """
    if not isinstance(saved, dict):
        return False
    return saved == _inputs() | _fx_inputs(saved.get("fx_cutoff"))


def build() -> CachedFrame:
    """Run a replay and build the ledger from it.

    Returns:
        A freshly computed `CachedFrame`, carrying the replay result.
    """
    return _build()[0]


def _build() -> tuple[CachedFrame, dict[str, object]]:
    """Run a replay, describing its inputs as they stood when each was read."""
    inputs = _inputs()
    rows = load_txn_rows()
    inputs |= _fx_inputs(max((row.fx_date for row in rows), default=None))
    resolver = load_symbol_resolver()
    result = replay(rows, load_fx_rates(), ReplayConfig.build(rows, resolver))
    return CachedFrame(frame=ledger_frame(result), result=result), inputs


def _read_cache(parquet: Path) -> CachedFrame | None:
    """Read the cached frame when the inputs it was built from are unchanged."""
    meta_path = _meta_path(parquet)
    if not parquet.exists() or not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.debug("Unreadable cost-base cache metadata; rebuilding")
        return None
    if not _is_current(meta.get("inputs")):
        logger.debug("Cost-base cache is stale; rebuilding")
        return None
    try:
        frame = pd.read_parquet(parquet, engine="fastparquet")
    except (OSError, ValueError):
        logger.debug("Unreadable cost-base cache; rebuilding")
        return None
    computed_at = datetime.fromisoformat(meta["computed_at"])
    snapshot = meta.get("replay")
    # Parquet stores no index, so restore the one the frame was built with.
    return CachedFrame(
        frame=index_by_txn_id(frame),
        computed_at=computed_at,
        result=decode_replay(snapshot) if snapshot else None,
    )


def _write_cache(
    parquet: Path,
    frame: pd.DataFrame,
    inputs: dict[str, object],
    result: ReplayResult | None = None,
) -> None:
    """Persist a freshly built frame alongside the inputs it was built from."""
    try:
        frame.to_parquet(parquet, engine="fastparquet", index=False)
        meta: dict[str, object] = {
            "inputs": inputs,
            "computed_at": datetime.now(TORONTO_TZ).isoformat(),
        }
        snapshot = encode_replay(result) if result is not None else None
        if snapshot is not None:
            meta["replay"] = snapshot
        _meta_path(parquet).write_text(json.dumps(meta), encoding="utf-8")
    except (OSError, ValueError):
        announce.warning(f"Could not write the cost-base cache to {parquet}")


def load_or_build(*, refresh: bool = False) -> CachedFrame:
    """Return the ledger, from cache when it is still valid.

    Args:
        refresh: Rebuild and rewrite the cache even if it looks current.

    Returns:
        The ledger, and when it was computed. A `computed_at` of `None`
        means this invocation built it.
    """
    parquet = get_config().acb_parquet
    # Opening the ledger is what upgrades a folio from before change tokens: its
    # tables get them here, so the first command after the upgrade replays.
    ensure_change_tokens()

    if not refresh:
        cached: CachedFrame | None = _read_cache(parquet)
        if cached is not None:
            return cached

    built, inputs = _build()
    _write_cache(parquet, built.frame, inputs, built.result)
    return built
