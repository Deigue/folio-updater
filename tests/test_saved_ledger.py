"""Tests for saving what the ledger frame does not carry.

The property that matters is that a reused ledger and a fresh replay are
indistinguishable to a reader.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from domain import Action, Currency, DiagnosticCode, Grain
from engine.accounts import FOLIO, Pool
from engine.ledger import Diagnosis, Ledger, open_ledger
from engine.saved_ledger import (
    decode_cash_and_totals,
    decode_deposit_shares,
    decode_diagnostics,
    decode_holdings,
    encode_cash_and_totals,
    encode_deposit_shares,
    encode_diagnostics,
    encode_holdings,
)

from .helpers.replay import make_row, run
from .helpers.seed import seed_cash, seed_fx, seed_transaction

if TYPE_CHECKING:
    from engine.types import ReplayResult
    from models import Txn

    from .helpers.context import TempContext


def oversold_rows() -> list[Txn]:
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
    shares = decode_deposit_shares(
        json.loads(json.dumps(encode_deposit_shares(ledger.deposit_shares))),
    )
    holdings = decode_holdings(json.loads(json.dumps(encode_holdings(ledger.holdings))))
    diagnosis = Diagnosis(diagnostics=tuple(diagnostics), rows=rows)
    restored = Ledger(
        frame=ledger.frame,
        cash=cash,
        totals=totals,
        load_diagnosis=lambda: diagnosis,
        deposit_shares=shares,
        holdings=holdings,
    )
    return ledger, restored


def test_every_diagnostic_round_trips_exactly() -> None:
    """Including one about a pool, which tags no transaction."""
    original, restored = round_trip(run(oversold_rows()))
    untagged = {d.code for d in original.diagnostics if d.txn_id is None}
    assert DiagnosticCode.NEGATIVE_FINAL_UNITS in untagged
    assert restored.diagnostics == original.diagnostics


def test_cash_round_trips_exactly() -> None:
    original, restored = round_trip(run(oversold_rows()))
    assert original.cash
    assert restored.cash == original.cash


def test_holdings_round_trip_exactly() -> None:
    """A cost base split in thirds, flagged, in USD: no float holds it all."""
    original, restored = round_trip(
        run(
            [
                *oversold_rows(),
                make_row(
                    4,
                    "2024-01-05",
                    Action.BUY,
                    amount="-100",
                    units="3",
                    price="33.33",
                    currency="USD",
                    ticker="USDCO",
                ),
                make_row(
                    5,
                    "2024-01-09",
                    Action.SELL,
                    amount="40",
                    units="-1",
                    price="40",
                    currency="USD",
                    ticker="USDCO",
                ),
            ],
        ),
    )
    held = dict(original.holdings)[FOLIO]
    assert any(holding.flags for holding in held)
    assert any(holding.currency is Currency.USD for holding in held)
    assert restored.holdings == original.holdings


def test_deposit_shares_round_trip_exactly() -> None:
    """A third of a pool's deposits, which no float would hold to the last digit."""
    original, restored = round_trip(
        run(
            [
                make_row(
                    1,
                    "2024-01-02",
                    Action.CONTRIBUTION,
                    amount="3000",
                    ticker=None,
                    account="QT-TFSA",
                ),
                make_row(
                    2,
                    "2024-03-01",
                    Action.TFR_OUT,
                    amount="-1000",
                    ticker=None,
                    account="QT-TFSA",
                ),
                make_row(
                    3,
                    "2024-03-01",
                    Action.TFR_IN,
                    amount="1000",
                    ticker=None,
                    account="WS-TFSA",
                ),
            ],
        ),
    )
    assert original.deposit_shares
    assert restored.deposit_shares == original.deposit_shares


@pytest.mark.real_saved_ledger
def test_a_reused_ledger_keeps_its_deposit_shares(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx({"2025-08-14": "1.25", "2025-08-18": "1.25"})
        seed_cash(
            "CONTRIBUTION",
            amount="10000",
            account="QT-TFSA",
            date="2025-08-14",
            settle_date="2025-08-14",
        )
        for action, amount, account in (
            ("TFR_OUT", "-4000", "QT-TFSA"),
            ("TFR_IN", "4000", "WS-TFSA"),
        ):
            seed_cash(
                action,
                amount=amount,
                account=account,
                date="2025-08-18",
                settle_date="2025-08-18",
            )

        replayed = open_ledger(offline=True)
        reused = open_ledger(offline=True)

        assert replayed.computed_at is None
        assert reused.computed_at is not None
        assert len(reused.deposit_shares) == 2
        assert reused.deposit_shares == replayed.deposit_shares


@pytest.mark.real_saved_ledger
def test_a_reused_ledger_keeps_its_holdings(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx({"2025-08-14": "1.25", "2025-08-15": "1.25", "2025-08-18": "1.25"})
        seed_transaction(ticker="MSFT", amount="-1000", price="100", units="10")

        replayed = open_ledger(offline=True)
        reused = open_ledger(offline=True)

        assert reused.computed_at is not None
        assert replayed.holdings
        assert reused.holdings == replayed.holdings


def test_the_row_counts_round_trip() -> None:
    original, restored = round_trip(run(oversold_rows()))
    assert restored.totals.rows == original.totals.rows
    assert restored.totals.accounts() == original.totals.accounts()


def test_money_survives_the_round_trip_to_the_last_digit() -> None:
    """Stored as text, never as a number: a float would round this off."""
    exact = "1234.12345678901234567890"
    _, restored = round_trip(
        run(
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
    result = run(oversold_rows())
    original, restored = round_trip(result)
    assert len(result.rows) == 3
    # Only the oversold SELL is diagnosed.
    assert list(original.diagnosed_rows) == [3]
    assert list(restored.diagnosed_rows) == [3]


def test_a_row_is_saved_once_however_many_diagnostics_tag_it() -> None:
    """An oversell is diagnosed at every grain, but its row is written once."""
    ledger = Ledger.from_replay(run(oversold_rows()))
    tagging = [d for d in ledger.diagnostics if d.txn_id == 3]
    saved = encode_diagnostics(ledger.diagnostics, ledger.diagnosed_rows)
    assert len(tagging) > 1
    assert saved["row_action"].notna().sum() == 1


def test_the_diagnosed_rows_round_trip_whole() -> None:
    """Including the per-grain measures, so no reader gets a half-built row."""
    original, restored = round_trip(run(oversold_rows()))
    assert restored.diagnosed_rows == original.diagnosed_rows


def test_a_clean_folio_saves_cash_and_counts_but_no_diagnostics() -> None:
    _, restored = round_trip(run(oversold_rows()[:2]))
    assert restored.diagnostics == ()
    assert restored.diagnosed_rows == {}
    # The cash totals and row counts still come across, which is what a report
    # needs even when nothing is wrong.
    assert restored.cash
    assert restored.totals.rows


def test_cash_keys_survive_a_pool_named_after_a_grain() -> None:
    """Grain, pool and currency stay three separate fields, never one string."""
    _, restored = round_trip(
        run(
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
    assert (Pool(Grain.ACCOUNT, "FOLIO"), Currency.CAD) in restored.cash
    assert (FOLIO, Currency.CAD) in restored.cash
