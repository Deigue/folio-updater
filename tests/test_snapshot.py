"""Tests for persisting a replay alongside the cached master frame.

The property that matters is that a cache hit and a fresh replay are
indistinguishable to a reader.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from engine.events import ReplayResult, TxnRow
from engine.fx_rates import FxRates
from engine.replay import ReplayConfig, detect_fee_signs, replay
from engine.snapshot import MAX_SNAPSHOT_ROWS, SCHEMA_VERSION, decode, encode
from services.symbols import SymbolResolver
from utils.constants import AccountType, Action, Currency, FeeConvention, Scope

if TYPE_CHECKING:
    from collections.abc import Sequence

D = Decimal

FLAT_FX = FxRates(("2020-01-01",), (D("1.35"),))


def make_row(
    txn_id: int,
    date: str,
    action: Action,
    *,
    amount: str = "0",
    units: str = "0",
    price: str = "0",
    fee: str = "0",
    currency: str = "CAD",
    ticker: str | None = "RY.TO",
    account: str = "IBKR-PERSONAL",
) -> TxnRow:
    """Build one transaction with every money column already Decimal."""
    return TxnRow(
        txn_id=txn_id,
        txn_date=date,
        settle_date=date,
        action=action,
        amount=D(amount),
        currency=Currency(currency),
        price=D(price),
        units=D(units),
        fee=D(fee),
        ticker=ticker,
        account=account,
    )


def replay_of(rows: Sequence[TxnRow]) -> ReplayResult:
    """Replay rows with explicit account facts, so no config is involved."""
    accounts = {row.account for row in rows}
    return replay(
        list(rows),
        FLAT_FX,
        ReplayConfig(
            account_types=dict.fromkeys(accounts, AccountType.NON_REGISTERED),
            fee_conventions=dict.fromkeys(accounts, FeeConvention.AUTO),
            symbols=SymbolResolver([]),
            fee_signs=detect_fee_signs(rows),
        ),
    )


def diagnosed_folio() -> ReplayResult:
    """Build a folio with an oversell, so the snapshot has something to carry."""
    return replay_of(
        [
            make_row(1, "2024-01-02", Action.CONTRIBUTION, amount="10000", ticker=None),
            make_row(
                2,
                "2024-01-03",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
            ),
            make_row(
                3,
                "2024-03-01",
                Action.SELL,
                amount="6000",
                units="-500",
                price="12",
            ),
        ],
    )


def snapshot_of(result: ReplayResult) -> dict[str, Any]:
    """Encode a replay, insisting it was small enough to store."""
    payload = encode(result)
    assert payload is not None
    return payload


def round_trip(result: ReplayResult) -> ReplayResult:
    """Encode a replay and read it straight back."""
    restored = decode(snapshot_of(result))
    assert restored is not None
    return restored


def test_a_snapshot_round_trips_every_diagnostic() -> None:
    original = diagnosed_folio()
    restored = round_trip(original)
    assert [w.code for w in restored.warnings] == [w.code for w in original.warnings]
    assert [w.detail for w in restored.warnings] == [
        w.detail for w in original.warnings
    ]
    assert [w.value for w in restored.warnings] == [w.value for w in original.warnings]
    assert [w.scope for w in restored.warnings] == [w.scope for w in original.warnings]


def test_a_snapshot_round_trips_cash_exactly() -> None:
    original = diagnosed_folio()
    restored = round_trip(original)
    assert restored.cash.keys() == original.cash.keys()
    for key, state in original.cash.items():
        assert restored.cash[key] == state


def test_a_snapshot_round_trips_the_row_counts() -> None:
    original = diagnosed_folio()
    restored = round_trip(original)
    assert restored.totals.rows == original.totals.rows
    assert restored.totals.accounts() == original.totals.accounts()


def test_money_survives_the_round_trip_to_the_last_digit() -> None:
    """Stored as text, never as a JSON number: a float would round this off."""
    exact = "1234.12345678901234567890"
    original = replay_of(
        [
            make_row(
                1,
                "2024-01-03",
                Action.BUY,
                amount=f"-{exact}",
                units="100",
                price="10",
            ),
            make_row(
                2,
                "2024-03-01",
                Action.SELL,
                amount="6000",
                units="-500",
                price="12",
            ),
        ],
    )
    restored = round_trip(original)
    kept = {row.row.txn_id: row for row in restored.rows}
    assert kept[1].row.amount == D(f"-{exact}")
    assert str(kept[1].row.amount) == f"-{exact}"


def test_a_snapshot_carries_only_the_rows_a_diagnostic_points_at() -> None:
    """The whole point: it stays small however long the ledger grows."""
    original = diagnosed_folio()
    payload = snapshot_of(original)
    assert len(original.rows) == 3
    # Only the oversold SELL is diagnosed.
    assert [row["txn_id"] for row in payload["rows"]] == [3]


def test_the_diagnosed_rows_round_trip_whole() -> None:
    """Including the per-scope measures, so no reader gets a half-built row."""
    original = diagnosed_folio()
    restored = round_trip(original)
    source = {row.row.txn_id: row for row in original.rows}
    for row in restored.rows:
        assert row == source[row.row.txn_id]


def test_a_snapshot_from_another_version_is_refused() -> None:
    payload = snapshot_of(diagnosed_folio())
    payload["schema"] = SCHEMA_VERSION + 1
    assert decode(payload) is None


def test_a_folio_with_too_many_findings_is_not_snapshotted() -> None:
    """Past the cap the reader replays, which is slower but always correct."""
    rows = [
        make_row(
            i,
            "2024-03-01",
            Action.SELL,
            amount="600",
            units="-50",
            price="12",
            ticker=f"S{i}",
        )
        for i in range(1, MAX_SNAPSHOT_ROWS + 2)
    ]
    assert encode(replay_of(rows)) is None


def test_a_clean_folio_snapshots_to_almost_nothing() -> None:
    original = replay_of(
        [
            make_row(1, "2024-01-02", Action.CONTRIBUTION, amount="10000", ticker=None),
            make_row(
                2,
                "2024-01-03",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
            ),
        ],
    )
    payload = snapshot_of(original)
    assert payload["rows"] == []
    assert payload["warnings"] == []
    # The cash totals and row counts still come across, which is what a report
    # needs even when nothing is wrong.
    assert payload["cash"]
    assert payload["totals"]


def test_totals_count_rows_by_shape() -> None:
    result = diagnosed_folio()
    assert result.totals.counting([Action.BUY]) == 1
    assert result.totals.counting([Action.BUY, Action.SELL]) == 2
    assert result.totals.counting([Action.SPLIT]) == 0
    assert result.totals.accounts() == {"IBKR-PERSONAL"}


def test_totals_respect_the_suppression_lists() -> None:
    result = diagnosed_folio()
    assert result.totals.counting([Action.BUY], {"IBKR-PERSONAL"}) == 0
    assert result.totals.counting([Action.BUY], (), {"RY.TO"}) == 0


def test_totals_split_a_security_by_currency() -> None:
    result = replay_of(
        [
            make_row(
                1,
                "2024-01-03",
                Action.BUY,
                amount="-1000",
                units="10",
                price="100",
                currency="USD",
                ticker="AAPL",
            ),
            make_row(
                2,
                "2024-02-03",
                Action.BUY,
                amount="-1100",
                units="10",
                price="110",
                currency="CAD",
                ticker="AAPL",
            ),
        ],
    )
    split = result.totals.currencies_for("AAPL", (Action.BUY,))
    assert split == {Currency.USD: 1, Currency.CAD: 1}


def test_cash_keys_survive_a_pool_named_after_a_scope() -> None:
    """Scope, pool and currency stay three separate fields, never one string."""
    original = replay_of(
        [
            make_row(
                1,
                "2024-01-02",
                Action.CONTRIBUTION,
                amount="10000",
                ticker=None,
                account="FOLIO",
            ),
        ],
    )
    restored = round_trip(original)
    assert (Scope.ACCOUNT, "FOLIO", Currency.CAD) in restored.cash
    assert (Scope.FOLIO, "FOLIO", Currency.CAD) in restored.cash
