"""Tests for saving what the ledger frame does not carry.

The property that matters is that a reused ledger and a fresh replay are
indistinguishable to a reader.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import TYPE_CHECKING

from domain import AccountType, Action, Currency, FeeConvention, Grain, WarningCode
from engine.fx_rates import FxRates
from engine.ledger import Diagnosis, Ledger
from engine.replay import ReplayConfig, detect_fee_signs, replay
from engine.snapshot import (
    decode_cash_and_totals,
    decode_diagnostics,
    encode_cash_and_totals,
    encode_diagnostics,
)
from engine.types import ReplayResult, TxnRow
from services.symbols import SymbolResolver

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


def oversold_rows() -> list[TxnRow]:
    """Build a folio with an oversell, so there is a diagnosed row to carry."""
    return [
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
    ]


def round_trip(result: ReplayResult) -> tuple[Ledger, Ledger]:
    """Build the ledger from a replay, then save and restore what the frame lacks.

    Returns:
        The ledger as replayed, and as read back.
    """
    ledger = Ledger.from_replay(result)
    diagnostics, rows = decode_diagnostics(
        encode_diagnostics(ledger.diagnostics, ledger.diagnosed_rows),
    )
    # Through JSON, as the meta file stores them.
    saved = json.loads(json.dumps(encode_cash_and_totals(ledger.cash, ledger.totals)))
    cash, totals = decode_cash_and_totals(saved)
    diagnosis = Diagnosis(diagnostics=tuple(diagnostics), rows=rows)
    restored = Ledger(
        frame=ledger.frame,
        cash=cash,
        totals=totals,
        load_diagnosis=lambda: diagnosis,
    )
    return ledger, restored


def test_every_diagnostic_round_trips_exactly() -> None:
    """Including one about a pool, which tags no transaction."""
    original, restored = round_trip(replay_of(oversold_rows()))
    untagged = {d.code for d in original.diagnostics if d.txn_id is None}
    assert WarningCode.NEGATIVE_FINAL_POSITION in untagged
    assert restored.diagnostics == original.diagnostics


def test_cash_round_trips_exactly() -> None:
    original, restored = round_trip(replay_of(oversold_rows()))
    assert original.cash
    assert restored.cash == original.cash


def test_the_row_counts_round_trip() -> None:
    original, restored = round_trip(replay_of(oversold_rows()))
    assert restored.totals.rows == original.totals.rows
    assert restored.totals.accounts() == original.totals.accounts()


def test_money_survives_the_round_trip_to_the_last_digit() -> None:
    """Stored as text, never as a number: a float would round this off."""
    exact = "1234.12345678901234567890"
    _, restored = round_trip(
        replay_of(
            [
                make_row(
                    1,
                    "2024-03-01",
                    Action.SELL,
                    amount=exact,
                    units="-500",
                    price="12",
                ),
            ],
        ),
    )
    assert str(restored.diagnosed_rows[1].row.amount) == exact


def test_only_the_rows_a_diagnostic_points_at_are_kept() -> None:
    """The whole point: it stays small however long the ledger grows."""
    result = replay_of(oversold_rows())
    original, restored = round_trip(result)
    assert len(result.rows) == 3
    # Only the oversold SELL is diagnosed.
    assert list(original.diagnosed_rows) == [3]
    assert list(restored.diagnosed_rows) == [3]


def test_a_row_is_saved_once_however_many_diagnostics_tag_it() -> None:
    """An oversell is diagnosed at every grain, but its row is written once."""
    ledger = Ledger.from_replay(replay_of(oversold_rows()))
    tagging = [d for d in ledger.diagnostics if d.txn_id == 3]
    saved = encode_diagnostics(ledger.diagnostics, ledger.diagnosed_rows)
    assert len(tagging) > 1
    assert saved["row_action"].notna().sum() == 1


def test_the_diagnosed_rows_round_trip_whole() -> None:
    """Including the per-grain measures, so no reader gets a half-built row."""
    original, restored = round_trip(replay_of(oversold_rows()))
    assert restored.diagnosed_rows == original.diagnosed_rows


def test_a_clean_folio_saves_cash_and_counts_but_no_diagnostics() -> None:
    _, restored = round_trip(replay_of(oversold_rows()[:2]))
    assert restored.diagnostics == ()
    assert restored.diagnosed_rows == {}
    # The cash totals and row counts still come across, which is what a report
    # needs even when nothing is wrong.
    assert restored.cash
    assert restored.totals.rows


def test_totals_count_rows_by_shape() -> None:
    result = replay_of(oversold_rows())
    assert result.totals.counting([Action.BUY]) == 1
    assert result.totals.counting([Action.BUY, Action.SELL]) == 2
    assert result.totals.counting([Action.SPLIT]) == 0
    assert result.totals.accounts() == {"IBKR-PERSONAL"}


def test_totals_respect_the_suppression_lists() -> None:
    result = replay_of(oversold_rows())
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


def test_cash_keys_survive_a_pool_named_after_a_grain() -> None:
    """Grain, pool and currency stay three separate fields, never one string."""
    _, restored = round_trip(
        replay_of(
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
        ),
    )
    assert (Grain.ACCOUNT, "FOLIO", Currency.CAD) in restored.cash
    assert (Grain.FOLIO, "FOLIO", Currency.CAD) in restored.cash
