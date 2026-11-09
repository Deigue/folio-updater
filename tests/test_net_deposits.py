"""Tests on the net deposits tracker itself, fed rows the way the replay feeds it."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from domain import AccountType, Action, Currency, Grain
from engine.accounts import Pool
from engine.deposits import NetDeposits
from engine.transfers import Crossing, pair_transfers
from engine.types import CashEffect, DepositShare, GrainMeasures, LedgerRow

from .helpers.replay import FLAT_FX, make_row

if TYPE_CHECKING:
    from models import Txn

D = Decimal
SENDER, RECEIVER = "QT-TFSA", "WS-TFSA"
SENDER_POOL, RECEIVER_POOL = Pool(Grain.ACCOUNT, SENDER), Pool(Grain.ACCOUNT, RECEIVER)
DAY = "2025-03-10"
CAD = Currency.CAD


def _ledger_row(row: Txn, delta_cad: str = "0") -> LedgerRow:
    """Wrap a transaction as the cost-base walk would, ACB at the account grain."""
    return LedgerRow(
        row=row,
        symbol=row.ticker,
        acct_type=AccountType.TFSA,
        impact=row.impact,
        fx_rate=None,
        fx_date=None,
        acct=GrainMeasures(delta_cad=D(delta_cad)),
    )


def _funded() -> list[tuple[LedgerRow, tuple[CashEffect, ...]]]:
    """Contribute 10,000 to the sender, then earn 10,000: a pool basis of 20,000."""
    return [
        (
            _ledger_row(
                make_row(
                    1,
                    "2025-01-02",
                    Action.CONTRIBUTION,
                    amount="10000",
                    ticker=None,
                    account=SENDER,
                ),
            ),
            (CashEffect(CAD, cash=D(10000), contributions=D(10000)),),
        ),
        (
            _ledger_row(
                make_row(
                    2,
                    "2025-02-03",
                    Action.DIVIDEND,
                    amount="10000",
                    ticker="XEQT.TO",
                    account=SENDER,
                ),
            ),
            (CashEffect(CAD, cash=D(10000), dividends=D(10000)),),
        ),
    ]


def _cash_legs() -> tuple[LedgerRow, LedgerRow]:
    """Move 5,000 of cash from the sender to the receiver: a quarter of its basis."""
    return (
        _ledger_row(
            make_row(
                3,
                DAY,
                Action.TFR_OUT,
                amount="-5000",
                ticker=None,
                account=SENDER,
            ),
        ),
        _ledger_row(
            make_row(
                4,
                DAY,
                Action.TFR_IN,
                amount="5000",
                ticker=None,
                account=RECEIVER,
            ),
        ),
    )


def _replayed(
    legs_in_order: tuple[LedgerRow, ...],
    earlier: tuple[tuple[LedgerRow, tuple[CashEffect, ...]], ...] = (),
) -> NetDeposits:
    """Feed the funding rows, any earlier ones, then the legs in the order given."""
    funded = [*_funded(), *earlier]
    rows = [computed for computed, _ in funded] + list(legs_in_order)
    matched, _unpaired = pair_transfers([computed.row for computed in rows])
    crossing = Crossing(SENDER_POOL, RECEIVER_POOL)
    pairs = [replace(pair, crossings=(crossing,)) for pair in matched]
    pair_of = {
        leg.txn_id: pair for pair in pairs for leg in (pair.out_leg, pair.in_leg)
    }
    tracker = NetDeposits(rows, pair_of, FLAT_FX)
    for computed, effects in funded:
        tracker.apply(computed, effects)
    for leg in legs_in_order:
        amount = leg.row.amount or D(0)
        tracker.apply(leg, (CashEffect(leg.row.currency, cash=amount),))
    return tracker


def _share(txn_id: int, pool: Pool, amount: str) -> DepositShare:
    """Build the account-grain share expected for one leg."""
    return DepositShare(txn_id, DAY, pool, CAD, D(amount))


def test_a_transfer_between_pools_moves_its_share_of_deposits() -> None:
    """5,000 is a quarter of a 20,000 pool basis, so 2,500 of 10,000 deposits moves."""
    tracker = _replayed(_cash_legs())

    assert tracker.shares == [
        _share(3, SENDER_POOL, "-2500"),
        _share(4, RECEIVER_POOL, "2500"),
    ]
    totals = tracker.closing_transfers()
    assert totals[(SENDER_POOL, CAD)].transfers == D(-2500)
    assert totals[(SENDER_POOL, CAD)].transfers_value == D(-5000)
    assert totals[(RECEIVER_POOL, CAD)].transfers == D(2500)
    assert totals[(RECEIVER_POOL, CAD)].transfers_value == D(5000)


def test_cash_staying_put_at_a_grain_leaves_its_pool_basis_alone() -> None:
    """Counted, this would halve the pool basis and double the share that moves."""
    staying = (
        _ledger_row(
            make_row(
                6,
                "2025-02-20",
                Action.FCH,
                amount="-10000",
                ticker=None,
                account=SENDER,
            ),
        ),
        (CashEffect(CAD, cash=D(-10000), grains=()),),
    )

    tracker = _replayed(_cash_legs(), earlier=(staying,))

    assert tracker.shares == [
        _share(3, SENDER_POOL, "-2500"),
        _share(4, RECEIVER_POOL, "2500"),
    ]


def test_an_in_leg_reached_first_settles_the_sender_once() -> None:
    out_leg, in_leg = _cash_legs()

    tracker = _replayed((in_leg, out_leg))

    assert tracker.shares == [
        _share(4, RECEIVER_POOL, "2500"),
        _share(3, SENDER_POOL, "-2500"),
    ]
    totals = tracker.closing_transfers()
    assert totals[(SENDER_POOL, CAD)].transfers == D(-2500)
    assert totals[(RECEIVER_POOL, CAD)].transfers == D(2500)


def test_an_in_kind_leg_is_measured_at_the_acb_it_carried() -> None:
    """5,000 of ACB leaving a 20,000 pool basis moves 2,500 of deposits."""
    legs = (
        _ledger_row(
            make_row(3, DAY, Action.TFR_OUT, units="-40", account=SENDER),
            delta_cad="-5000",
        ),
        _ledger_row(
            make_row(4, DAY, Action.TFR_IN, units="40", account=RECEIVER),
            delta_cad="5000",
        ),
    )
    tracker = _replayed(legs)

    assert tracker.shares == [
        _share(3, SENDER_POOL, "-2500"),
        _share(4, RECEIVER_POOL, "2500"),
    ]
    totals = tracker.closing_transfers()
    assert totals[(SENDER_POOL, CAD)].transfers_value == D(-5000)


def test_an_unpaired_cash_leg_counts_at_face_value_at_every_grain() -> None:
    leg = _ledger_row(
        make_row(5, DAY, Action.TFR_IN, amount="750", ticker=None, account=RECEIVER),
    )
    tracker = NetDeposits([leg], {}, FLAT_FX)

    tracker.apply(leg, (CashEffect(CAD, cash=D(750)),))

    assert sorted(share.amount for share in tracker.shares) == [D(750)] * len(Grain)
    totals = tracker.closing_transfers()
    assert all(entry.transfers == D(750) for entry in totals.values())
    assert all(entry.transfers_value == D(750) for entry in totals.values())
    assert len(totals) == len(Grain)


def test_an_unpaired_cash_leg_joins_the_deposits_of_a_tracked_pool() -> None:
    """10,000 contributed, 2,500 sent on, 700 arriving unmatched: 8,200 held."""
    unmatched = _ledger_row(
        make_row(5, DAY, Action.TFR_IN, amount="700", ticker=None, account=SENDER),
    )

    tracker = _replayed((*_cash_legs(), unmatched))

    totals = tracker.closing_transfers()
    assert totals[(SENDER_POOL, CAD)].transfers == D(-1800)


def test_an_unpaired_in_kind_leg_moves_no_deposits() -> None:
    leg = _ledger_row(make_row(5, DAY, Action.TFR_IN, units="40", account=RECEIVER))
    tracker = NetDeposits([leg], {}, FLAT_FX)

    tracker.apply(leg, ())

    assert tracker.shares == []
    assert tracker.closing_transfers() == {}


def test_an_unpaired_leg_of_nothing_records_no_share() -> None:
    leg = _ledger_row(make_row(5, DAY, Action.TFR_OUT, ticker=None, account=SENDER))
    tracker = NetDeposits([leg], {}, FLAT_FX)

    tracker.apply(leg, (CashEffect(CAD),))

    assert tracker.shares == []
