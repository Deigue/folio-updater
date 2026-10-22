"""Transaction Ledger: every transaction alongside the figures computed for it.

The saved ledger is reused only when every input the replay reads is unchanged:

- the transactions and ticker aliases, through the change tokens the database
  moves on every write to them;
- the FX rates a conversion could use, summarised up to the latest date the
  folio converts at, saved with the ledger;
- the whole `accounts` section of the config;
- the format the ledger was saved in.

Each save writes the ledger frame and the diagnostics (with the rows they tag)
under names of its own, then a meta file naming them, holding those inputs, when
the ledger was computed, and each pool's cash and row counts. The meta goes in
place last, whole, so it only ever names files that were written completely:
an interrupted save leaves the previous one intact, and two processes saving at
once can never pair one's meta with the other's files. Files no meta names are
removed after each save.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from typing import TYPE_CHECKING, Any

import pandas as pd

from app import get_config
from db.queries import get_connection, get_fx_summary, get_max_value, get_revisions
from db.schema import ensure_change_tokens
from domain import TORONTO_TZ, Column, Table
from engine.frames import index_by_txn_id, ledger_frame
from engine.fx_rates import FxRateUnavailableError, load_fx_rates
from engine.replay import ReplayConfig, replay
from engine.snapshot import (
    SCHEMA_VERSION,
    decode_cash_and_totals,
    decode_diagnostics,
    encode_cash_and_totals,
    encode_diagnostics,
)
from engine.types import load_txn_rows
from services import ForexService
from services.symbols import load_symbol_resolver
from term import announce

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from engine.types import (
        CashKey,
        CashState,
        ComputedRow,
        ReplayResult,
        ReplayTotals,
        ReplayWarning,
    )

logger = logging.getLogger(__name__)

# Parquet stores every saved diagnostic field as text, blanks included.
_TEXT = "utf8"

# What restoring a saved ledger raises when a file reads but does not decode.
_UNDECODABLE = (ValueError, KeyError, ArithmeticError)


class LedgerUnavailableError(RuntimeError):
    """The folio cannot be replayed: a stored transaction cannot be converted."""


@dataclass(frozen=True)
class Diagnosis:
    """What a replay found wrong with the folio.

    Attributes:
        diagnostics: Every diagnostic the replay raised, in the order raised.
        rows: The exact computed row of each transaction a diagnostic tags,
            by TxnId.
    """

    diagnostics: tuple[ReplayWarning, ...]
    rows: Mapping[int, ComputedRow]


@dataclass(frozen=True, eq=False)
class Ledger:
    """Every transaction in the folio alongside the figures computed for it.

    Attributes:
        frame: Every transaction with its figures at every grain, in display
            form, indexed by TxnId.
        cash: The running cash and flows of every pool, per currency.
        totals: How many rows carry each shape, for the checks to count.
        load_diagnosis: Reads the diagnostics when first asked for, so a report
            that never asks never pays for them.
        computed_at: When the ledger was computed, or None when this
            invocation replayed it.
    """

    frame: pd.DataFrame
    cash: Mapping[CashKey, CashState]
    totals: ReplayTotals
    load_diagnosis: Callable[[], Diagnosis] = field(repr=False)
    computed_at: datetime | None = None

    @classmethod
    def from_replay(cls, result: ReplayResult) -> Ledger:
        """Lay out a completed replay as the ledger.

        Args:
            result: The replay.

        Returns:
            The ledger, keeping only the rows a diagnostic tags beside its frame.
        """
        diagnosed = {
            diagnostic.txn_id
            for diagnostic in result.warnings
            if diagnostic.txn_id is not None
        }
        diagnosis = Diagnosis(
            diagnostics=tuple(result.warnings),
            rows={
                computed.row.txn_id: computed
                for computed in result.rows
                if computed.row.txn_id in diagnosed
            },
        )
        return cls(
            frame=ledger_frame(result),
            cash=result.cash,
            totals=result.totals,
            load_diagnosis=lambda: diagnosis,
        )

    @cached_property
    def diagnosis(self) -> Diagnosis:
        """What the replay found wrong, read once on first use.

        Raises:
            LedgerUnavailableError: If the saved diagnostics were unreadable and
                the folio could no longer be replayed to recover them.
        """
        return self.load_diagnosis()

    @property
    def diagnostics(self) -> tuple[ReplayWarning, ...]:
        """Every diagnostic the replay raised, in the order raised."""
        return self.diagnosis.diagnostics

    @property
    def diagnosed_rows(self) -> Mapping[int, ComputedRow]:
        """The exact computed row of each transaction a diagnostic tags."""
        return self.diagnosis.rows


def open_ledger(
    *,
    refresh: bool = False,
    priced: bool = False,
    offline: bool = False,
) -> Ledger:
    """Open the ledger, reusing the saved one while it is still current.

    FX rates are topped up first, when the config allows it. A report that prices
    the folio needs today's rate to convert today's quotes; any other needs rates
    only through the folio's latest settlement date, so it does not reach for the
    network just because the calendar moved on. A failed download only warns.

    Args:
        refresh: Replay and save even if the saved ledger is current.
        priced: Top up FX rates through today rather than the folio's latest
            settlement date.
        offline: Never touch the network: use the FX rates stored.

    Returns:
        The ledger.

    Raises:
        LedgerUnavailableError: If a stored transaction cannot be converted.
    """
    if not offline:
        _top_up_fx(priced=priced)
    # Opening the ledger is what upgrades a folio from before change tokens: its
    # tables get them here, so the first command after the upgrade replays.
    ensure_change_tokens()

    parquet = get_config().acb_parquet
    if not refresh and (saved := _read(parquet)) is not None:
        return saved
    return _replay(parquet)


def _top_up_fx(*, priced: bool) -> None:
    """Fetch the FX rates the folio lacks, when the config allows it."""
    if not get_config().auto_getfx:
        return
    earliest = ForexService.get_earliest_transaction_date()
    if earliest is None:
        return
    latest = None
    if not priced:
        with get_connection() as conn:
            latest = get_max_value(conn, Table.TXNS, Column.Txn.SETTLE_DATE)
    try:
        ForexService.ensure_coverage(earliest, latest)
    except (OSError, ValueError, KeyError):
        announce.warning("Could not refresh FX rates; using what is stored")


def _replay(parquet: Path) -> Ledger:
    """Replay the folio and save the ledger.

    Raises:
        LedgerUnavailableError: If a stored transaction cannot be converted.
    """
    try:
        ledger, inputs = _build()
    except FxRateUnavailableError as error:
        raise LedgerUnavailableError(str(error)) from error
    _write(parquet, ledger, inputs)
    return ledger


# -- INPUTS ---------------------------------------------------------------------


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


def _build() -> tuple[Ledger, dict[str, object]]:
    """Replay the folio, describing its inputs as they stood when each was read."""
    inputs = _inputs()
    rows = load_txn_rows()
    inputs |= _fx_inputs(max((row.fx_date for row in rows), default=None))
    resolver = load_symbol_resolver()
    result = replay(rows, load_fx_rates(), ReplayConfig.build(rows, resolver))
    return Ledger.from_replay(result), inputs


# -- SAVED LEDGER ---------------------------------------------------------------


def _meta_path(parquet: Path) -> Path:
    """Where the meta naming the saved ledger's files is stored."""
    return parquet.with_suffix(".meta.json")


def _frame_path(parquet: Path, save: str) -> Path:
    """Where one save stores the ledger frame."""
    return parquet.with_name(f"{parquet.stem}.{save}{parquet.suffix}")


def _diagnostics_path(parquet: Path, save: str) -> Path:
    """Where one save stores the diagnostics."""
    return parquet.with_name(f"{parquet.stem}.{save}.diagnostics{parquet.suffix}")


def _read(parquet: Path) -> Ledger | None:
    """Read the saved ledger when the inputs it was built from are unchanged."""
    meta = _read_meta(parquet)
    if meta is None or not _is_current(meta.get("inputs")):
        logger.debug("No current saved ledger; replaying")
        return None
    save = meta["save"]
    frame = _read_frame(parquet, save)
    if frame is None:
        return None
    try:
        return _restore(parquet, save, frame, meta)
    except _UNDECODABLE:
        logger.debug("Undecodable saved ledger; replaying")
        return None


def _restore(
    parquet: Path,
    save: str,
    frame: pd.DataFrame,
    meta: Mapping[str, Any],
) -> Ledger:
    """Rebuild a ledger from its saved form, leaving the diagnostics on disk."""
    cash, totals = decode_cash_and_totals(meta)

    def load_diagnosis() -> Diagnosis:
        try:
            diagnostics, rows = decode_diagnostics(_read_diagnostics(parquet, save))
        except (OSError, *_UNDECODABLE):
            logger.debug("Unreadable saved diagnostics; replaying")
            return _replay(parquet).diagnosis
        return Diagnosis(diagnostics=tuple(diagnostics), rows=rows)

    return Ledger(
        # Parquet stores no index, so restore the one the frame was built with.
        frame=index_by_txn_id(frame),
        cash=cash,
        totals=totals,
        load_diagnosis=load_diagnosis,
        computed_at=datetime.fromisoformat(meta["computed_at"]),
    )


def _write(parquet: Path, ledger: Ledger, inputs: dict[str, object]) -> None:
    """Save a freshly replayed ledger alongside the inputs it was built from."""
    meta = {
        "inputs": inputs,
        "save": secrets.token_hex(6),
        "computed_at": datetime.now(TORONTO_TZ).isoformat(),
        **encode_cash_and_totals(ledger.cash, ledger.totals),
    }
    diagnostics = encode_diagnostics(ledger.diagnostics, ledger.diagnosed_rows)
    try:
        _write_files(parquet, ledger.frame, diagnostics, meta)
    except (OSError, ValueError):
        announce.warning(f"Could not save the ledger to {parquet.parent}")


def _read_meta(parquet: Path) -> dict[str, Any] | None:
    """Read the saved ledger's meta, or None when there is no readable one."""
    try:
        meta = json.loads(_meta_path(parquet).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def _read_frame(parquet: Path, save: str) -> pd.DataFrame | None:
    """Read one save's ledger frame, or None when it is gone or unreadable."""
    try:
        return pd.read_parquet(_frame_path(parquet, save), engine="fastparquet")
    except (OSError, ValueError):
        logger.debug("Unreadable saved ledger; replaying")
        return None


def _read_diagnostics(parquet: Path, save: str) -> pd.DataFrame:
    """Read one save's diagnostics."""
    return pd.read_parquet(_diagnostics_path(parquet, save), engine="fastparquet")


def _write_files(
    parquet: Path,
    frame: pd.DataFrame,
    diagnostics: pd.DataFrame,
    meta: dict[str, Any],
) -> None:
    """Save the ledger's files under the meta's save, then put the meta in place.

    Whatever stops the save part way, the files it wrote are removed, and the
    meta in place still names the previous save's files.
    """
    save = meta["save"]
    meta_path = _meta_path(parquet)
    staged = meta_path.with_name(f"{meta_path.name}.{save}.tmp")
    kept = (_frame_path(parquet, save), _diagnostics_path(parquet, save))
    try:
        frame.to_parquet(kept[0], engine="fastparquet", index=False)
        diagnostics.to_parquet(
            kept[1],
            engine="fastparquet",
            index=False,
            object_encoding=_TEXT,
        )
        staged.write_text(json.dumps(meta), encoding="utf-8")
        staged.replace(meta_path)
    except BaseException:
        for path in (*kept, staged):
            path.unlink(missing_ok=True)
        raise
    _remove_other_saves(parquet, kept)


def _remove_other_saves(parquet: Path, kept: tuple[Path, ...]) -> None:
    """Remove the files of every save but the one the meta now names.

    A file still open elsewhere is left for the next save to remove.
    """
    for path in parquet.parent.glob(f"{parquet.stem}.*parquet"):
        if path in kept:
            continue
        try:
            path.unlink()
        except OSError:
            logger.debug("Could not remove %s; left for the next save", path)
