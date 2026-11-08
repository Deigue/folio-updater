"""The adjusted cost base report: one pool's rows and holdings."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import cached_property
from typing import TYPE_CHECKING, NamedTuple

from domain import Column, Currency, CurrencyColumns, DiagnosticCode, Grain, Impact
from engine.frames import (
    POOL_COLUMN,
    acb_summary_frame,
    grain_column,
    index_by_txn_id,
    replay_ordered,
)
from engine.gain_since_buy import with_gains_since_buy
from engine.pools import FOLIO, Pool

if TYPE_CHECKING:
    from collections.abc import Mapping

    import pandas as pd

    from engine.fx_rates import FxRates
    from models import Quote

_TXN_ID = str(Column.Txn.TXN_ID)
_TXN_DATE = str(Column.Txn.TXN_DATE)
_SETTLE_DATE = str(Column.Txn.SETTLE_DATE)
_CURRENCY = str(Column.Txn.CURRENCY)
_SYMBOL = str(Column.Ledger.SYMBOL)
_FLAGS = str(Column.Ledger.FLAGS)

# Running measures whose direction of travel is worth marking.
_DIRECTED_MEASURES = ("Units", "Avg", "Avg_USD")

# The columns gain since buy fills in or adds, at every grain.
_GAIN_COLUMNS = tuple(
    grain_column(grain, f"{measure}{suffix}")
    for grain in Grain
    for measure in ("Gain", "GainPct")
    for suffix in ("", "_USD")
)


class NoUsdFiguresError(ValueError):
    """USD columns alone were asked for where nothing is USD-denominated."""

    MESSAGE = "Nothing here is in USD, so there are no USD figures."

    def __init__(self) -> None:
        """Carry the one message this error ever has."""
        super().__init__(self.MESSAGE)


class AcbShape(StrEnum):
    """Which of the three cost-base reports is laid out."""

    BUILDUP = "buildup"  # One symbol's transactions, in settlement-date order
    SUMMARY = "summary"  # One row per symbol: the cost base summary
    LEDGER = "ledger"  # Every transaction picked, in replay order


class Currencies(NamedTuple):
    """Which currency variants of the figures apply.

    Attributes:
        cad: Whether the CAD columns apply.
        usd: Whether the USD columns apply.
    """

    cad: bool
    usd: bool


@dataclass(frozen=True, eq=False)
class AcbReport:
    """One pool's cost base, as the terminal and the sheets lay it out.

    Build one with `build`. Nothing in it changes: `priced` returns a new report.

    Attributes:
        pool: The pool reported.
        shape: Which report it is.
        symbol: The one security asked about, when one was.
        currencies: Whether the CAD and USD columns apply.
        rows: The transactions picked, oldest first: settlement-date order for a
            buildup, replay order otherwise. A view only ever reverses them.
        source: The whole ledger the rows were picked from.
        stored_columns: Columns the stored transactions added to `rows`.
    """

    pool: Pool
    shape: AcbShape
    symbol: str | None
    currencies: Currencies
    rows: pd.DataFrame
    source: pd.DataFrame = field(repr=False)
    stored_columns: tuple[str, ...] = ()

    @classmethod
    def build(  # noqa: PLR0913
        cls,
        frame: pd.DataFrame,
        pool: Pool,
        shape: AcbShape,
        *,
        symbol: str | None = None,
        dates: tuple[str | None, str | None] = (None, None),
        currencies: CurrencyColumns = CurrencyColumns.BOTH,
        income: bool = False,
        txns: pd.DataFrame | None = None,
    ) -> AcbReport:
        """Pick a pool's rows and decide which currency columns apply.

        Args:
            frame: The whole ledger.
            pool: The pool to report.
            shape: Which report to build. A buildup needs a symbol.
            symbol: Only this security's rows.
            dates: Only rows traded on or after the first date and on or before
                the second; either may be None.
            currencies: The currency columns asked for.
            income: Whether DIVIDEND and FCH rows are included.
            txns: The stored transactions. Those the replay dropped are listed
                too, with the columns they were stored with and none of the
                replay's, unless a pool, symbol or date range leaves them out.

        Returns:
            The report.

        Raises:
            NoUsdFiguresError: If USD alone was asked for where nothing shown is
                in USD: no holding traded in USD for a summary, no row with USD as
                its transaction currency otherwise.
        """
        stored: tuple[str, ...] = ()
        source = frame
        if txns is not None:
            frame, stored = _with_stored_columns(frame, txns)
        picked = _pick(frame, pool, symbol, dates, income=income)
        usd = bool(
            _usd_holdings(picked)
            if shape is AcbShape.SUMMARY
            else (picked[_CURRENCY] == str(Currency.USD)).any(),
        )
        if currencies is CurrencyColumns.USD and not usd:
            raise NoUsdFiguresError
        wanted = Currencies(
            cad=currencies is not CurrencyColumns.USD,
            usd=currencies is CurrencyColumns.USD
            or (currencies is CurrencyColumns.BOTH and usd),
        )
        rows = (
            picked.sort_values([_SETTLE_DATE, _TXN_ID], kind="stable")
            if shape is AcbShape.BUILDUP
            else replay_ordered(picked)
        )
        return cls(pool, shape, symbol, wanted, rows, source, stored)

    @classmethod
    def for_every_pool(cls, frame: pd.DataFrame) -> list[AcbReport]:
        """Summarise the folio, then each account type, then each account, in CAD.

        Args:
            frame: The whole ledger.

        Returns:
            One cost base summary per pool the ledger has rows for.
        """
        pools = [FOLIO]
        for grain in (Grain.TYPE, Grain.ACCOUNT):
            names = sorted({str(name) for name in frame[POOL_COLUMN[grain]].dropna()})
            pools.extend(Pool(grain, name) for name in names)
        return [
            cls.build(
                frame,
                pool,
                AcbShape.SUMMARY,
                currencies=CurrencyColumns.CAD,
                income=True,
            )
            for pool in pools
        ]

    @cached_property
    def holdings(self) -> pd.DataFrame:
        """The cost base summary: one row per symbol, open holdings first.

        Asked for USD alone, the holdings never traded in USD are left out: they
        have no USD figures to show.
        """
        summary = acb_summary_frame(self.rows)
        if summary.empty:
            return summary
        if self.currencies.usd and not self.currencies.cad:
            summary = summary[summary[_SYMBOL].isin(_usd_holdings(self.rows))]
        units = grain_column(self.pool.grain, "Units")
        ordered = summary.assign(_closed=summary[units].fillna(0) == 0)
        return (
            ordered.sort_values(["_closed", _SYMBOL], kind="stable")
            .drop(columns="_closed")
            .reset_index(drop=True)
        )

    @property
    def data(self) -> pd.DataFrame:
        """What another program reads: `holdings` for a summary, else `rows`."""
        return self.holdings if self.shape is AcbShape.SUMMARY else self.rows

    @cached_property
    def directions(self) -> Mapping[tuple[str, int], int]:
        """Which way Units, Avg and Avg_USD moved on each row.

        Measured in replay order against the symbol's previous row, each currency
        tracked apart: a rate move can push the CAD average one way and the USD
        average the other.

        Returns:
            `(measure, TxnId)` to `+1` where the measure rose and `-1` where it
            fell. A measure that held still is absent, as is a symbol's first row.
        """
        ordered = replay_ordered(self.rows)
        moves: dict[tuple[str, int], int] = {}
        for measure in _DIRECTED_MEASURES:
            column = grain_column(self.pool.grain, measure)
            tracked = ordered[[_SYMBOL, _TXN_ID, column]].dropna()
            change = tracked.groupby(_SYMBOL)[column].diff()
            moved = change.notna() & (change != 0)
            for txn_id, delta in zip(
                tracked[_TXN_ID][moved],
                change[moved],
                strict=True,
            ):
                moves[(measure, int(txn_id))] = 1 if delta > 0 else -1
        return moves

    @cached_property
    def flag_counts(self) -> Mapping[str, int]:
        """Count each flag on the rows, sorted by code.

        A cost base summary leaves out the superficial loss flag: pooled across a
        symbol it cannot say which sale it was on.
        """
        skipped = (
            str(DiagnosticCode.SUPERFICIAL_LOSS_SUSPECT)
            if self.shape is AcbShape.SUMMARY
            else ""
        )
        counts: dict[str, int] = {}
        for value in self.rows[_FLAGS].fillna(""):
            for code in str(value).split(","):
                if code and code != skipped:
                    counts[code] = counts.get(code, 0) + 1
        return dict(sorted(counts.items()))

    def priced(self, quotes: Mapping[str, Quote], fx_rates: FxRates) -> AcbReport:
        """Work out each buy's gain since buy, and every row's Gain%.

        Worked out over the whole ledger, since a split any account reported
        counts. The report never fetches quotes: the caller does.

        Args:
            quotes: Canonical symbol to its quote. A buy with no price stays blank.
            fx_rates: Rates to convert a quote at today's rate.

        Returns:
            A new report, its rows carrying the gains.
        """
        valued = with_gains_since_buy(self.source, quotes, fx_rates)
        gains = valued.reindex(self.rows.index)
        rows = self.rows.copy()
        for column in _GAIN_COLUMNS:
            rows[column] = gains[column]
        return replace(self, rows=rows)


def _usd_holdings(rows: pd.DataFrame) -> set[str]:
    """Name the holdings with a USD cost base: those traded in USD.

    Income never counts: a CAD holding paying its dividend in USD still has
    nothing but a CAD cost base.
    """
    traded = rows[
        rows[_SYMBOL].notna()
        & (rows[_CURRENCY] == str(Currency.USD))
        & (rows[str(Column.Ledger.IMPACT)] != Impact.INCOME)
    ]
    return set(traded[_SYMBOL])


def _pick(
    frame: pd.DataFrame,
    pool: Pool,
    symbol: str | None,
    dates: tuple[str | None, str | None],
    *,
    income: bool,
) -> pd.DataFrame:
    """Narrow the ledger to the rows a request asked for."""
    rows = frame
    if pool.grain is not Grain.FOLIO:
        rows = rows[rows[POOL_COLUMN[pool.grain]] == pool.name]
    if symbol is not None:
        rows = rows[rows[_SYMBOL] == symbol]
    if not income:
        # A dividend never touches the cost base, so it is noise in a buildup.
        rows = rows[rows[str(Column.Ledger.IMPACT)] != Impact.INCOME]
    date_from, date_to = dates
    if date_from:
        rows = rows[rows[_TXN_DATE] >= date_from]
    if date_to:
        rows = rows[rows[_TXN_DATE] <= date_to]
    return rows


def _with_stored_columns(
    frame: pd.DataFrame,
    txns: pd.DataFrame,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    """Join the stored columns the ledger leaves out, keyed on `TxnId`.

    Every stored transaction is listed, so one the replay dropped still is, with
    the columns it was stored with and none of the replay's.

    Returns:
        The joined rows, and the names of the columns the join added.
    """
    extra = tuple(
        column
        for column in txns.columns
        if column not in frame.columns and column != _TXN_ID
    )
    # The frame's figures win; a transaction the replay dropped has none, so
    # it keeps what was stored.
    joined = frame.set_index(_TXN_ID).combine_first(txns.set_index(_TXN_ID))
    return index_by_txn_id(joined.reset_index()), extra
