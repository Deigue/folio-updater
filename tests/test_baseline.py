"""The demo folio's figures, saved, so a change shows which ones it moves.

`tests/baselines/` holds every figure the replay and the valuation give the
demo folio: each transaction's measures at every grain, each pool's cash and
flows, the replay's row counts, what `folio dash` reads for every pool in each
display currency, the gains since buy and the cost base summaries `folio acb`
reads (at the end, and as of a year-end), what `folio ticker` adds for each
security, and the workbook's room history. The folio is valued as of the demo's
last day, at that day's closes and FX rate, so no figure depends on the day the
test runs.

A failure lists the figures that moved. A change that should move nothing
fixes the code, never the baseline. A change that means to move figures (a fix
to the cost base, a new demo case, a price history refresh) rewrites the
baseline in the same change, and its git diff is the review:

    uv run pytest tests/test_baseline.py --update-baseline

Read what moved before staging it: the rewrite always passes.
"""

from __future__ import annotations

import csv
import inspect
import math
from bisect import bisect_right
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app import get_config
from datagen import ensure_data_exists
from datagen.demo import CRA_ROOM, END, SYMBOLS, currency_of, price_on
from domain import Action, Column, Currency, Grain, Impact
from engine import CashState, FxRates, load_fx_rates
from engine.flows import room_history
from engine.frames import (
    GRAIN_PREFIX,
    acb_summary_frame,
    acb_summary_frames_by_pool,
    grain_column,
)
from engine.gain_since_buy import bought_symbols, with_gains_since_buy
from engine.ledger import open_ledger
from engine.panels import FOLIO_VIEW, FolioValuation, account_view, type_view
from engine.ticker import symbol_holdings
from services.quotes_service import QuotesService
from services.symbols import SymbolResolver, load_symbol_resolver

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Iterator

    import pandas as pd
    from _typeshed import DataclassInstance

    from engine.holdings import DisplayCurrency
    from engine.ledger import Ledger
    from engine.panels import Panel

    from .helpers.context import TempContext

BASELINES = Path(__file__).parent / "baselines"
# How many moved figures a failure spells out before it only counts the rest.
_SHOWN_MOVES = 30
# A transaction is named by these, then by its place among those sharing them.
_TXN_KEY = [
    str(Column.Txn.TXN_DATE),
    str(Column.Txn.ACCOUNT),
    str(Column.Txn.ACTION),
    str(Column.Txn.TICKER),
]
_PLACE = "Nth"
# Each currency `folio dash -c` can show the folio in.
_DISPLAY_CURRENCIES: tuple[DisplayCurrency, ...] = (
    Currency.CAD,
    Currency.USD,
    "native",
)
# A year-end inside the demo, for the cost base as `folio acb --to` reports it.
_YEAR_END = "2025-12-31"


@dataclass(frozen=True)
class _Table:
    """Figures as one baseline file holds them.

    Attributes:
        header: Column names, the key's first.
        rows: One list of cells per row, in file order.
        key_width: How many leading columns name a row.
    """

    header: list[str]
    rows: list[list[str]]
    key_width: int


# -- the demo, valued as of its last day ----------------------------------------


@pytest.fixture
def cached_quote_data() -> dict[str, dict[str, Any]]:
    """Quote every demo symbol at its close on the demo's last day.

    Overrides the suite's stand-in provider, keyed by provider symbol.

    Returns:
        Provider symbol mapped to its raw price and metadata fields.
    """
    resolver = SymbolResolver(())
    return {
        resolver.provider_symbol(symbol): {
            "price": float(price_on(symbol, END)),
            "prev_close": float(price_on(symbol, END.replace(day=END.day - 1))),
            "currency": str(currency_of(symbol)),
            "name": symbol,
            "sector": None,
            "exchange": None,
            "market_cap": None,
        }
        for symbol in SYMBOLS
    }


def _rates_through_end() -> FxRates:
    """Read the folio's FX rates up to the demo's last day, so it is the latest.

    No helper cuts `FxRates` at a date: valuation always wants every rate held.
    """
    fx = load_fx_rates()
    cut = bisect_right(fx.dates, END.isoformat())
    return FxRates(fx.dates[:cut], fx.usdcad[:cut])


@pytest.fixture
def demo_ledger(temp_ctx: TempContext) -> Generator[Ledger, Any]:
    """Open the demo folio's ledger, with FX rates held only up to its last day.

    The copied demo skips the build that writes its contribution room, so the
    config is given the same room a real build writes.

    Yields:
        The replayed demo ledger, inside its temp context.
    """
    with (
        temp_ctx(contribution_room=CRA_ROOM),
        patch("engine.panels.load_fx_rates", side_effect=_rates_through_end),
    ):
        ensure_data_exists()
        yield open_ledger(offline=True)


# -- turning figures into rows --------------------------------------------------


def _cell(value: object) -> str:
    """Write one figure exactly, so any change to it shows."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (tuple, list)):
        return ",".join(_cell(item) for item in value)
    return str(value)


def _txn_table(frame: pd.DataFrame, figures: list[str]) -> _Table:
    """Name each transaction readably and write out the figures asked for.

    Args:
        frame: Ledger rows, one per transaction.
        figures: The columns to write after the key.

    Returns:
        The table, in key order.
    """
    ordered = frame.sort_values(str(Column.Txn.TXN_ID))
    places = ordered.groupby(_TXN_KEY, dropna=False).cumcount()
    rows = [
        [*(_cell(txn[column]) for column in _TXN_KEY), str(place)]
        + [_cell(txn[column]) for column in figures]
        for (_, txn), place in zip(ordered.iterrows(), places, strict=True)
    ]
    width = len(_TXN_KEY) + 1
    rows.sort(key=lambda row: (row[: width - 1], int(row[width - 1])))
    return _Table([*_TXN_KEY, _PLACE, *figures], rows, width)


def _is_container(value: object) -> bool:
    """Whether a value is a collection of records, which are written on their own."""
    return isinstance(value, (list, tuple)) and any(map(is_dataclass, value))


def _figures(
    obj: DataclassInstance,
    prefix: str = "",
) -> Iterator[tuple[str, object]]:
    """Name every figure a dataclass holds or works out, nested ones included.

    Args:
        obj: The record.
        prefix: Prepended to each name, for a nested record.

    Yields:
        Each figure's name and value: fields first, then properties.
    """
    properties = [
        name
        for name, _ in inspect.getmembers(type(obj), lambda m: isinstance(m, property))
    ]
    for name in [field.name for field in fields(obj)] + properties:
        value = getattr(obj, name)
        if _is_container(value):
            continue
        if isinstance(value, dict):
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
                yield f"{prefix}{name} {_cell(key)}", item
        elif is_dataclass(value) and not isinstance(value, type):
            yield from _figures(value, f"{prefix}{name}.")
        else:
            yield f"{prefix}{name}", value


def _record_rows(key: list[str], record: DataclassInstance) -> list[list[str]]:
    """One row per figure of a record, each led by the record's key."""
    return [[*key, name, _cell(value)] for name, value in _figures(record)]


def _every_pool(valuation: FolioValuation) -> list[Panel]:
    """Value the folio, each account type and each account."""
    return [
        *valuation.panels([FOLIO_VIEW]),
        *valuation.panels([type_view(name) for name in valuation.account_types()]),
        *valuation.panels([account_view(name) for name in valuation.accounts()]),
    ]


def _dashboard_rows(display: str, panels: Iterable[Panel]) -> list[list[str]]:
    """Write out every figure the dashboard reads from each pool."""
    rows: list[list[str]] = []
    for panel in panels:
        pool = [display, str(panel.view.grain), panel.view.pool]
        held = panel.holdings
        records: list[tuple[str, DataclassInstance]] = [
            *((f"holding {holding.symbol}", holding) for holding in held.holdings),
            *((f"holding {holding.symbol}", holding) for holding in held.closed),
            ("totals", held),
            *((f"totals {group.currency}", group) for group in held.by_currency),
            ("flows", panel.flows),
        ]
        for item, record in records:
            rows.extend(_record_rows([*pool, item], record))
    return rows


def _summary_rows(as_of: str, frame: pd.DataFrame) -> list[list[str]]:
    """Write out each pool's cost base summary, as `folio acb --summary` reads it.

    Args:
        as_of: The last transaction date the summary counts.
        frame: The ledger.

    Returns:
        One row per pool and symbol, carrying that grain's measures only.
    """
    shown = frame[
        (frame[Column.Ledger.IMPACT] != Impact.INCOME)
        & (frame[str(Column.Txn.TXN_DATE)] <= as_of)
    ]
    pools: list[tuple[Grain, str, pd.DataFrame]] = [
        (Grain.FOLIO, FOLIO_VIEW.pool, acb_summary_frame(shown)),
    ]
    for grain in (Grain.TYPE, Grain.ACCOUNT):
        pools += [
            (grain, pool, summary)
            for pool, summary in acb_summary_frames_by_pool(shown, grain).items()
        ]
    rows: list[list[str]] = []
    for grain, pool, summary in pools:
        prefix = GRAIN_PREFIX[grain]
        measures = [column for column in summary.columns if column.startswith(prefix)]
        rows += [
            [as_of, str(grain), pool, str(record[Column.Ledger.SYMBOL])]
            + [_cell(record[measure]) for measure in measures]
            for record in summary.to_dict("records")
        ]
    return rows


# -- comparing against the saved baseline ---------------------------------------


def _moved(saved: _Table, table: _Table) -> list[str]:
    """List every figure that differs from the saved baseline.

    Args:
        saved: The baseline as read from its file.
        table: The figures as they are now.

    Returns:
        One line per removed or added row, then per moved figure, naming its row
        key and column with the value before and after.
    """
    width = table.key_width

    def by_key(found: _Table) -> dict[tuple[str, ...], dict[str, str]]:
        return {
            tuple(row[:width]): dict(zip(found.header, row, strict=True))
            for row in found.rows
        }

    before, after = by_key(saved), by_key(table)
    columns = list(dict.fromkeys([*saved.header[width:], *table.header[width:]]))
    lines = [f"removed: {' | '.join(key)}" for key in before if key not in after]
    lines += [f"added: {' | '.join(key)}" for key in after if key not in before]
    for key in sorted(before.keys() & after.keys()):
        old, new = before[key], after[key]
        row = " | ".join(key)
        lines += [
            f"{row} | {column}: {old.get(column, '')} -> {new.get(column, '')}"
            for column in columns
            if old.get(column, "") != new.get(column, "")
        ]
    return lines


def _check(request: pytest.FixtureRequest, name: str, table: _Table) -> None:
    """Compare figures with their saved baseline, or save them when asked to.

    Args:
        request: The running test, for `--update-baseline`.
        name: The baseline file, without its `.csv`.
        table: The figures as they are now.
    """
    path = BASELINES / f"{name}.csv"
    if request.config.getoption("--update-baseline"):  # pragma: no cover
        BASELINES.mkdir(exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file, lineterminator="\n")
            writer.writerow(table.header)
            writer.writerows(table.rows)
        return
    if not path.exists():  # pragma: no cover
        pytest.fail(
            f"No baseline {path.name}: run with --update-baseline",
            pytrace=False,
        )
    with path.open(newline="", encoding="utf-8") as file:
        header, *rows = list(csv.reader(file))
    moved = _moved(_Table(header, rows, table.key_width), table)
    if moved:  # pragma: no cover
        shown = "\n".join(moved[:_SHOWN_MOVES])
        rest = len(moved) - _SHOWN_MOVES
        more = f"\n... and {rest} more" if rest > 0 else ""
        pytest.fail(
            f"{len(moved)} figures moved from {path.name}:\n{shown}{more}\n"
            "If this change means to move them: "
            "uv run pytest tests/test_baseline.py --update-baseline",
            pytrace=False,
        )


# -- the baseline ---------------------------------------------------------------


def test_every_transaction_keeps_its_figures(
    request: pytest.FixtureRequest,
    demo_ledger: Ledger,
) -> None:
    frame = demo_ledger.frame
    figures = [
        column
        for column in frame.columns
        if column not in {*_TXN_KEY, str(Column.Txn.TXN_ID)}
    ]

    _check(request, "ledger", _txn_table(frame, figures))


def test_every_pool_keeps_its_cash_and_row_counts(
    request: pytest.FixtureRequest,
    demo_ledger: Ledger,
) -> None:
    measures = [field.name for field in fields(CashState)]
    cash = sorted(
        [str(grain), pool, str(currency)]
        + [_cell(getattr(state, measure)) for measure in measures]
        for (grain, pool, currency), state in demo_ledger.cash.items()
    )
    counts = sorted(
        [_cell(action), account, symbol, _cell(currency), str(count)]
        for (action, account, symbol, currency), count in (
            demo_ledger.totals.rows.items()
        )
    )

    _check(
        request,
        "cash",
        _Table(["Grain", "Pool", "Currency", *measures], cash, 3),
    )
    _check(
        request,
        "row_counts",
        _Table(["Action", "Account", "Symbol", "Currency", "Rows"], counts, 4),
    )


def test_every_dashboard_pool_keeps_its_figures(
    request: pytest.FixtureRequest,
    demo_ledger: Ledger,
) -> None:
    rows = [
        row
        for display in _DISPLAY_CURRENCIES
        for row in _dashboard_rows(
            str(display),
            _every_pool(FolioValuation.build(demo_ledger, currency=display)),
        )
    ]
    header = ["Display", "Grain", "Pool", "Item", "Figure", "Value"]

    _check(request, "dashboard", _Table(header, sorted(rows), 5))


def test_every_security_keeps_what_folio_ticker_adds(
    request: pytest.FixtureRequest,
    demo_ledger: Ledger,
) -> None:
    """Each pool's holding is the dashboard's; this keeps the rest of the report."""
    valuation = FolioValuation.build(demo_ledger)
    symbols = sorted(set(demo_ledger.frame[Column.Ledger.SYMBOL].dropna()))
    rows: list[list[str]] = []
    for symbol in symbols:
        for by_account in (False, True):
            found = symbol_holdings(valuation, symbol, by_account=by_account)
            assert found is not None, symbol
            key = [symbol, "account" if by_account else "type"]
            rows += [
                row
                for row in _record_rows(key, found)
                if not row[2].startswith("total.")
            ]
            pools = [line.view.pool for line in found.lines]
            rows.append([*key, "lines", _cell(pools)])
            rows.append([*key, "held_for", _cell(found.held_for(END))])

    _check(
        request,
        "ticker",
        _Table(["Symbol", "Lines", "Figure", "Value"], sorted(rows), 3),
    )


def test_every_year_of_contribution_room_keeps_its_figures(
    request: pytest.FixtureRequest,
    demo_ledger: Ledger,
) -> None:
    """The workbook's room history, run to the demo's last year, not today's."""
    valuation = FolioValuation.build(demo_ledger)
    history = room_history(
        valuation.contributions,
        valuation.withdrawals,
        get_config().contribution_room,
        through=END.year,
    )
    rows = [
        row
        for room in history
        for row in _record_rows([str(room.account_type), str(room.year)], room)
        if row[2] not in {"account_type", "year"}
    ]

    _check(
        request,
        "room",
        _Table(["AccountType", "Year", "Figure", "Value"], sorted(rows), 3),
    )


def test_every_gain_since_buy_keeps_its_figures(
    request: pytest.FixtureRequest,
    demo_ledger: Ledger,
) -> None:
    frame = demo_ledger.frame
    quotes = QuotesService.get_quotes(bought_symbols(frame), load_symbol_resolver())
    valued = with_gains_since_buy(frame, quotes, _rates_through_end())
    trades = valued[valued[str(Column.Txn.ACTION)].isin([Action.BUY, Action.SELL])]
    figures = [
        grain_column(grain, f"{measure}{suffix}")
        for grain in Grain
        for measure in ("Gain", "GainPct")
        for suffix in ("", "_USD")
    ]

    _check(request, "acb", _txn_table(trades, figures))


def test_every_pool_keeps_its_cost_base_summary(
    request: pytest.FixtureRequest,
    demo_ledger: Ledger,
) -> None:
    """At the end, and as of a year-end the way `folio acb --to` cuts it."""
    frame = demo_ledger.frame
    rows = [
        *_summary_rows(_YEAR_END, frame),
        *_summary_rows(END.isoformat(), frame),
    ]
    prefix = GRAIN_PREFIX[Grain.FOLIO]
    measures = [
        column.removeprefix(prefix)
        for column in acb_summary_frame(frame).columns
        if column.startswith(prefix)
    ]
    header = ["AsOf", "Grain", "Pool", "Symbol", *measures]

    _check(request, "summary", _Table(header, sorted(rows), 4))
