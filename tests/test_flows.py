"""Tests for reading cash, contributions and contribution room out of a replay."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from domain import AccountType, Action, Column, Currency, Scope
from engine.cache import build
from engine.flows import (
    ROOM_BEARING_TYPES,
    Flows,
    Room,
    build_flows,
    contributions_by_type_year,
)

from .helpers.seed import seed_fx, seed_transaction

if TYPE_CHECKING:
    from .test_types import TempContext

FX_DATES = {
    "2025-08-14": "1.25",
    "2025-08-15": "1.25",
    "2025-08-18": "1.25",
    "2025-08-19": "1.25",
    "2025-08-20": "1.25",
}


def _flows(
    scope: Scope = Scope.FOLIO,
    pool: str | None = None,
    account_type: AccountType | None = None,
    room: dict | None = None,
    year: int | None = None,
) -> Flows:
    """Replay whatever was seeded and read one pool's flows."""
    cached = build()
    assert cached.result is not None
    return build_flows(
        cached.result,
        scope=scope,
        pool=pool,
        label=pool or "Portfolio",
        contributions=contributions_by_type_year(cached.frame),
        account_type=account_type,
        room=room,
        year=year,
    )


def test_cash_is_reported_per_currency_never_summed(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            ticker=None,
            currency="CAD",
            amount="1000",
            price=None,
            units=None,
        )
        seed_transaction(
            action="CONTRIBUTION",
            ticker=None,
            currency="USD",
            amount="500",
            price=None,
            units=None,
        )

        flows = _flows()

        assert flows.cash[Currency.CAD] == Decimal(1000)
        assert flows.cash[Currency.USD] == Decimal(500)


def test_a_transfer_moves_cash_without_consuming_room(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            ticker=None,
            currency="CAD",
            amount="1000",
            price=None,
            units=None,
        )
        seed_transaction(
            action="TFR_IN",
            ticker=None,
            currency="CAD",
            amount="5000",
            price=None,
            units=None,
        )

        flows = _flows()

        # Both moved cash...
        assert flows.cash[Currency.CAD] == Decimal(6000)
        # ...but only the contribution counts toward room.
        assert flows.contributions[Currency.CAD] == Decimal(1000)


def test_withdrawals_are_tracked_apart_from_transfers(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            ticker=None,
            currency="CAD",
            amount="5000",
            price=None,
            units=None,
        )
        seed_transaction(
            action="WITHDRAWAL",
            ticker=None,
            currency="CAD",
            amount="-1200",
            price=None,
            units=None,
            date="2025-08-18",
        )
        seed_transaction(
            action="TFR_OUT",
            ticker=None,
            currency="CAD",
            amount="-800",
            price=None,
            units=None,
            date="2025-08-19",
        )

        flows = _flows()

        assert flows.withdrawals[Currency.CAD] == Decimal(1200)
        assert flows.cash[Currency.CAD] == Decimal(3000)


def test_net_deposited_is_contributions_minus_withdrawals(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            ticker=None,
            currency="CAD",
            amount="5000",
            price=None,
            units=None,
        )
        seed_transaction(
            action="WITHDRAWAL",
            ticker=None,
            currency="CAD",
            amount="-1200",
            price=None,
            units=None,
            date="2025-08-18",
        )

        flows = _flows()

        assert flows.net_deposited[Currency.CAD] == Decimal(3800)


def test_net_deposited_nets_a_cash_only_transfer(temp_ctx: TempContext) -> None:
    """A cash-only TFR_OUT genuinely reduces what this account holds.

    Unlike contribution room, `Net Deposited` answers "how much of my own money is still
    here", and a same-owner institutional transfer out is real money leaving this
    specific pool.
    """
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            ticker=None,
            currency="CAD",
            amount="10000",
            price=None,
            units=None,
        )
        seed_transaction(
            action="TFR_OUT",
            ticker=None,
            currency="CAD",
            amount="-4000",
            price=None,
            units=None,
            date="2025-08-18",
        )
        # A position leg of a transfer moves no cash, so it must not count.
        seed_transaction(
            action="TFR_OUT",
            ticker="AAA",
            currency="CAD",
            amount=None,
            price=None,
            units="-5",
            date="2025-08-18",
        )

        flows = _flows()

        assert flows.net_deposited[Currency.CAD] == Decimal(6000)


def test_an_in_kind_transfer_carries_net_deposits_between_accounts(
    temp_ctx: TempContext,
) -> None:
    """Securities arriving are worth what they cost, not nothing.

    An account funded by an in-kind transfer holds real value that its owner
    paid for. Counting only cash would read that account as empty.
    """
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            account="QT-TFSA",
            ticker=None,
            currency="CAD",
            amount="1000",
            price=None,
            units=None,
        )
        seed_transaction(
            account="QT-TFSA",
            ticker="AAA",
            currency="CAD",
            amount="-1000",
            price="10",
            units="100",
        )
        seed_transaction(
            action="TFR_OUT",
            account="QT-TFSA",
            ticker="AAA",
            currency="CAD",
            amount=None,
            price=None,
            units="-100",
            date="2025-08-18",
        )
        seed_transaction(
            action="TFR_IN",
            account="WS-TFSA",
            ticker="AAA",
            currency="CAD",
            amount=None,
            price=None,
            units="100",
            date="2025-08-18",
        )

        sender = _flows(Scope.ACCOUNT, "QT-TFSA")
        receiver = _flows(Scope.ACCOUNT, "WS-TFSA")

        # The cost base left one account and arrived at the other.
        assert sender.net_deposited[Currency.CAD] == Decimal(0)
        assert receiver.net_deposited[Currency.CAD] == Decimal(1000)


def test_an_in_kind_transfer_cancels_at_the_pooled_grains(
    temp_ctx: TempContext,
) -> None:
    """Moving a holding between accounts you own deposits nothing."""
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            account="QT-TFSA",
            ticker=None,
            currency="CAD",
            amount="1000",
            price=None,
            units=None,
        )
        seed_transaction(
            account="QT-TFSA",
            ticker="AAA",
            currency="CAD",
            amount="-1000",
            price="10",
            units="100",
        )
        seed_transaction(
            action="TFR_OUT",
            account="QT-TFSA",
            ticker="AAA",
            currency="CAD",
            amount=None,
            price=None,
            units="-100",
            date="2025-08-18",
        )
        seed_transaction(
            action="TFR_IN",
            account="WS-TFSA",
            ticker="AAA",
            currency="CAD",
            amount=None,
            price=None,
            units="100",
            date="2025-08-18",
        )

        folio = _flows()

        assert folio.transfers == {}
        assert folio.net_deposited[Currency.CAD] == Decimal(1000)


def test_a_currency_journal_leaves_net_deposits_alone(
    temp_ctx: TempContext,
) -> None:
    """Norbert's Gambit converts money, it does not deposit any.

    The two legs are deliberately denominated differently, so booking each in
    its own currency would read the conversion as a USD deposit paired with a
    CAD withdrawal. Neither happened.
    """
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            account="QT-RRSP",
            ticker=None,
            currency="CAD",
            amount="1250",
            price=None,
            units=None,
        )
        seed_transaction(
            account="QT-RRSP",
            ticker="DLR.TO",
            currency="CAD",
            amount="-1250",
            price="12.50",
            units="100",
        )
        # Journal the CAD units into their USD twin, the gambit itself.
        seed_transaction(
            action="TFR_OUT",
            account="QT-RRSP",
            ticker="DLR.TO",
            currency="CAD",
            amount=None,
            price=None,
            units="-100",
            date="2025-08-18",
        )
        seed_transaction(
            action="TFR_IN",
            account="IBKR-RRSP",
            ticker="DLR.U.TO",
            currency="USD",
            amount=None,
            price=None,
            units="100",
            date="2025-08-18",
        )

        folio = _flows()

        # The contribution is the only deposit; the conversion added nothing.
        assert folio.net_deposited == {Currency.CAD: Decimal(1250)}


def _seed_grown_then_swept(out_account: str, in_account: str) -> None:
    """Fund an account, double the money, then sweep some cash to another.

    10,000 contributed buys 100 units at 100, which sell for 15,000. The pool is
    then worth 15,000 against 10,000 of deposits, so a 7,500 sweep is half of it
    and must carry half the deposits: 5,000, not the 7,500 its face value says.
    """
    seed_fx(FX_DATES)
    seed_transaction(
        action="CONTRIBUTION",
        account=out_account,
        ticker=None,
        currency="CAD",
        amount="10000",
        price=None,
        units=None,
        date="2025-08-14",
        settle_date="2025-08-14",
    )
    seed_transaction(
        account=out_account,
        ticker="AAA",
        currency="CAD",
        amount="-10000",
        price="100",
        units="100",
        date="2025-08-14",
        settle_date="2025-08-14",
    )
    seed_transaction(
        action="SELL",
        account=out_account,
        ticker="AAA",
        currency="CAD",
        amount="15000",
        price="150",
        units="100",
        date="2025-08-15",
        settle_date="2025-08-15",
    )
    seed_transaction(
        action="TFR_OUT",
        account=out_account,
        ticker=None,
        currency="CAD",
        amount="-7500",
        price=None,
        units=None,
        date="2025-08-18",
        settle_date="2025-08-18",
    )
    seed_transaction(
        action="TFR_IN",
        account=in_account,
        ticker=None,
        currency="CAD",
        amount="7500",
        price=None,
        units=None,
        date="2025-08-18",
        settle_date="2025-08-18",
    )


def test_a_swept_cash_transfer_moves_deposits_not_face_value(
    temp_ctx: TempContext,
) -> None:
    """Cash raised by selling at a profit is not all a returned deposit.

    Sell a position for a 5,000 gain and move 7,500 of the proceeds to another broker
    Face value says 7500 left, but actually only 5000 worth of deposits moves.
    """
    with temp_ctx():
        _seed_grown_then_swept("QT-TFSA", "WS-TFSA")

        sender = _flows(Scope.ACCOUNT, "QT-TFSA")
        receiver = _flows(Scope.ACCOUNT, "WS-TFSA")

        # Half the pool left, so half the deposits went with it.
        assert sender.net_deposited[Currency.CAD] == Decimal(5000)
        assert receiver.net_deposited[Currency.CAD] == Decimal(5000)


def test_a_swept_cash_transfer_leaves_the_pooled_grains_alone(
    temp_ctx: TempContext,
) -> None:
    """Moving money between two accounts you own deposits nothing.

    On the folio level, and moving between same account types, deposits dont change.
    """
    with temp_ctx():
        _seed_grown_then_swept("QT-TFSA", "WS-TFSA")

        folio = _flows()
        by_type = _flows(Scope.TYPE, str(AccountType.TFSA))

        assert folio.transfers == {}
        assert by_type.transfers == {}
        assert folio.net_deposited == {Currency.CAD: Decimal(10000)}
        assert by_type.net_deposited == {Currency.CAD: Decimal(10000)}


def test_a_full_transfer_carries_exactly_what_was_contributed(
    temp_ctx: TempContext,
) -> None:
    """Emptying an account moves its deposits, and only its deposits."""
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            account="QT-TFSA",
            ticker=None,
            currency="CAD",
            amount="10000",
            price=None,
            units=None,
            date="2025-08-14",
            settle_date="2025-08-14",
        )
        seed_transaction(
            account="QT-TFSA",
            ticker="AAA",
            currency="CAD",
            amount="-10000",
            price="100",
            units="100",
            date="2025-08-14",
            settle_date="2025-08-14",
        )
        seed_transaction(
            action="DIVIDEND",
            account="QT-TFSA",
            ticker="AAA",
            currency="CAD",
            amount="500",
            price=None,
            units=None,
            date="2025-08-15",
            settle_date="2025-08-15",
        )
        seed_transaction(
            action="TFR_OUT",
            account="QT-TFSA",
            ticker="AAA",
            currency="CAD",
            amount=None,
            price=None,
            units="-100",
            date="2025-08-18",
            settle_date="2025-08-18",
        )
        seed_transaction(
            action="TFR_IN",
            account="WS-TFSA",
            ticker="AAA",
            currency="CAD",
            amount=None,
            price=None,
            units="100",
            date="2025-08-18",
            settle_date="2025-08-18",
        )
        seed_transaction(
            action="TFR_OUT",
            account="QT-TFSA",
            ticker=None,
            currency="CAD",
            amount="-500",
            price=None,
            units=None,
            date="2025-08-19",
            settle_date="2025-08-19",
        )
        seed_transaction(
            action="TFR_IN",
            account="WS-TFSA",
            ticker=None,
            currency="CAD",
            amount="500",
            price=None,
            units=None,
            date="2025-08-19",
            settle_date="2025-08-19",
        )

        sender = _flows(Scope.ACCOUNT, "QT-TFSA")
        receiver = _flows(Scope.ACCOUNT, "WS-TFSA")

        # The dividend was growth, not a deposit, so it moves none of itself.
        assert sender.net_deposited[Currency.CAD] == Decimal(0)
        assert receiver.net_deposited[Currency.CAD] == Decimal(10000)


def test_a_partial_in_kind_transfer_counts_uninvested_cash_in_the_pool(
    temp_ctx: TempContext,
) -> None:
    """The share leaving is measured against the whole pool, cash included.

    10,000 buys the position and a 500 dividend lands beside it, so the pool is
    worth 10,500 while the position is only 10,000 of it. Moving half the units
    is 5,000 of 10,500, not 5,000 of 10,000, and the difference is exactly the
    growth that must stay behind.
    """
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            account="QT-TFSA",
            ticker=None,
            currency="CAD",
            amount="10000",
            price=None,
            units=None,
            date="2025-08-14",
            settle_date="2025-08-14",
        )
        seed_transaction(
            account="QT-TFSA",
            ticker="AAA",
            currency="CAD",
            amount="-10000",
            price="100",
            units="100",
            date="2025-08-14",
            settle_date="2025-08-14",
        )
        seed_transaction(
            action="DIVIDEND",
            account="QT-TFSA",
            ticker="AAA",
            currency="CAD",
            amount="500",
            price=None,
            units=None,
            date="2025-08-15",
            settle_date="2025-08-15",
        )
        seed_transaction(
            action="TFR_OUT",
            account="QT-TFSA",
            ticker="AAA",
            currency="CAD",
            amount=None,
            price=None,
            units="-50",
            date="2025-08-18",
            settle_date="2025-08-18",
        )
        seed_transaction(
            action="TFR_IN",
            account="WS-TFSA",
            ticker="AAA",
            currency="CAD",
            amount=None,
            price=None,
            units="50",
            date="2025-08-18",
            settle_date="2025-08-18",
        )

        receiver = _flows(Scope.ACCOUNT, "WS-TFSA")
        sender = _flows(Scope.ACCOUNT, "QT-TFSA")

        # 5,000 of a 10,500 pool: 10000 * 5000/10500.
        moved = Decimal(10000) * Decimal(5000) / Decimal(10500)
        assert receiver.net_deposited[Currency.CAD] == moved
        assert sender.net_deposited[Currency.CAD] == Decimal(10000) - moved
        # Ignoring the uninvested cash would have moved a round 5,000 instead.
        assert receiver.net_deposited[Currency.CAD] != Decimal(5000)


def test_a_transfer_cannot_carry_more_deposits_than_the_pool_holds(
    temp_ctx: TempContext,
) -> None:
    """A missing transaction must not manufacture deposits out of nothing.

    Cash gone negative leaves book value below the cost base the transfer is
    about to remove, so the share leaving computes above one. Unclamped it would
    credit the receiver with deposits that never existed and push the sender
    below zero, and the error would then propagate through every later transfer.
    """
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            account="QT-TFSA",
            ticker=None,
            currency="CAD",
            amount="1000",
            price=None,
            units=None,
            date="2025-08-14",
            settle_date="2025-08-14",
        )
        seed_transaction(
            account="QT-TFSA",
            ticker="AAA",
            currency="CAD",
            amount="-1000",
            price="10",
            units="100",
            date="2025-08-14",
            settle_date="2025-08-14",
        )
        # A charge with no funding row behind it: cash goes negative.
        seed_transaction(
            action="FCH",
            account="QT-TFSA",
            ticker=None,
            currency="CAD",
            amount="-200",
            price=None,
            units=None,
            date="2025-08-15",
            settle_date="2025-08-15",
        )
        seed_transaction(
            action="TFR_OUT",
            account="QT-TFSA",
            ticker="AAA",
            currency="CAD",
            amount=None,
            price=None,
            units="-100",
            date="2025-08-18",
            settle_date="2025-08-18",
        )
        seed_transaction(
            action="TFR_IN",
            account="WS-TFSA",
            ticker="AAA",
            currency="CAD",
            amount=None,
            price=None,
            units="100",
            date="2025-08-18",
            settle_date="2025-08-18",
        )

        sender = _flows(Scope.ACCOUNT, "QT-TFSA")
        receiver = _flows(Scope.ACCOUNT, "WS-TFSA")

        # The whole pool left, so the whole deposit did, and no more.
        assert sender.net_deposited[Currency.CAD] == Decimal(0)
        assert receiver.net_deposited[Currency.CAD] == Decimal(1000)


def test_an_unpaired_leg_falls_back_to_face_value(temp_ctx: TempContext) -> None:
    """A leg with no counterpart has no pool to measure a share against.

    The proportional rule needs a sending pool, so a leg without one degrades to
    the face value it booked before the rule existed rather than silently
    contributing nothing. `folio check` reports the unpaired leg separately.
    """
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="TFR_IN",
            account="WS-TFSA",
            ticker=None,
            currency="CAD",
            amount="5000",
            price=None,
            units=None,
            date="2025-08-18",
            settle_date="2025-08-18",
        )

        receiver = _flows(Scope.ACCOUNT, "WS-TFSA")

        assert receiver.net_deposited[Currency.CAD] == Decimal(5000)


def test_negative_cash_is_surfaced_as_an_alert(temp_ctx: TempContext) -> None:
    """The highest-value alert available without a real brokerage balance."""
    with temp_ctx():
        seed_fx(FX_DATES)
        # A buy with no contribution funding it: cash goes below zero, which
        # means a transaction is missing.
        seed_transaction(ticker="AAA", currency="CAD", amount="-1000", units="10")

        flows = _flows()

        assert flows.cash[Currency.CAD] < 0
        assert flows.negative_currencies == (Currency.CAD,)


def test_a_sub_cent_residue_is_not_a_missing_transaction() -> None:
    """FX conversion leaves dust; the alert must not fire on it.

    A folio reconciled to the cent can still land on balances like
    -0.000003 CAD after converting hundreds of rows. Firing "a transaction is
    probably missing" on that trains the user to ignore the one alert that
    matters most.
    """
    dusty = Flows(
        scope=Scope.FOLIO,
        pool="",
        label="Portfolio",
        cash={Currency.CAD: Decimal("-0.000002999")},
    )
    genuinely_short = Flows(
        scope=Scope.FOLIO,
        pool="",
        label="Portfolio",
        cash={Currency.CAD: Decimal("-0.01")},
    )

    assert dusty.negative_currencies == ()
    assert genuinely_short.negative_currencies == (Currency.CAD,)


def _funded(**deposits: str) -> Flows:
    """Build a pool carrying nothing but the named contributions."""
    return Flows(
        scope=Scope.FOLIO,
        pool="",
        label="Portfolio",
        contributions={
            Currency[code]: Decimal(amount) for code, amount in deposits.items()
        },
    )


def test_the_return_denominator_is_the_cad_money_put_in() -> None:
    assert _funded(CAD="10000").net_deposit_denominator == Decimal(10000)


def test_a_folio_funded_only_in_usd_has_no_return_denominator() -> None:
    """CAD only: joining a USD deposit to the sum needs a rate that would lie.

    Today's rate rewrites a historical deposit and the settle-date rate is not
    carried for cash movements, so a percentage here would be invented. No
    denominator means a blank cell, which is the honest answer.
    """
    assert _funded(USD="10000").net_deposit_denominator is None


def test_an_account_emptied_by_a_transfer_has_no_return_denominator() -> None:
    """An account whose whole balance moved holds none of its own money.

    Its deposits net to zero, and the return on nothing is not a number.
    """
    flows = _funded(CAD="10000")
    flows.transfers[Currency.CAD] = Decimal(-10000)

    assert flows.net_deposited[Currency.CAD] == Decimal(0)
    assert flows.net_deposit_denominator is None


def test_a_sub_cent_deposit_residue_is_not_a_denominator() -> None:
    """The proportional transfer split can leave a fraction of a cent behind.

    Dividing by 1e-23 is arithmetically fine and reports a return in the
    trillions. Judged at cent precision, exactly as `negative_currencies` is,
    because a figure below a cent is not money and must not act like it.
    """
    flows = _funded(CAD="10000")
    flows.transfers[Currency.CAD] = Decimal(-10000) + Decimal("1E-23")

    # Non-zero, and dividing 1,000 of gains by it would report 1e25 percent.
    assert flows.net_deposited[Currency.CAD] != Decimal(0)
    assert flows.net_deposit_denominator is None


def test_a_healthy_pool_raises_no_cash_alert(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            ticker=None,
            currency="CAD",
            amount="5000",
            price=None,
            units=None,
        )
        seed_transaction(
            ticker="AAA",
            currency="CAD",
            amount="-1000",
            units="10",
            date="2025-08-18",
        )

        assert _flows().negative_currencies == ()


def test_contributions_split_by_calendar_year(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx({**FX_DATES, "2024-08-15": "1.25", "2024-08-19": "1.25"})
        for date, amount in (("2024-08-15", "3000"), ("2025-08-15", "7000")):
            seed_transaction(
                action="CONTRIBUTION",
                account="WS-TFSA",
                ticker=None,
                currency="CAD",
                amount=amount,
                price=None,
                units=None,
                date=date,
            )

        cached = build()
        by_year = contributions_by_type_year(cached.frame)[AccountType.TFSA]

        assert by_year == {2024: Decimal(3000), 2025: Decimal(7000)}


def test_the_year_split_reads_the_frame_not_the_replay_rows(
    temp_ctx: TempContext,
) -> None:
    """A cache hit rebuilds the replay from a snapshot holding only diagnosed rows.

    Walking `ReplayResult.rows` would therefore miss most contributions on a
    cached run, so the split must come off the master frame, which is always
    1:1 with `Txns`.
    """
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            account="WS-TFSA",
            ticker=None,
            currency="CAD",
            amount="7000",
            price=None,
            units=None,
        )

        cached = build()
        assert cached.result is not None
        # Simulate the shape a cache hit produces: no rows carried at all.
        cached.result.rows = []

        flows = build_flows(
            cached.result,
            scope=Scope.TYPE,
            pool=str(AccountType.TFSA),
            label="TFSA",
            contributions=contributions_by_type_year(cached.frame),
            account_type=AccountType.TFSA,
            room={AccountType.TFSA: {2025: Decimal(7000)}},
            year=2025,
        )

        assert flows.room is not None
        assert flows.room.used == Decimal(7000)


def test_room_reports_used_against_the_configured_limit(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            account="WS-TFSA",
            ticker=None,
            currency="CAD",
            amount="5000",
            price=None,
            units=None,
        )

        flows = _flows(
            Scope.TYPE,
            str(AccountType.TFSA),
            AccountType.TFSA,
            {AccountType.TFSA: {2025: Decimal(7000)}},
            2025,
        )

        assert flows.room is not None
        assert flows.room.used == Decimal(5000)
        assert flows.room.limit == Decimal(7000)
        assert flows.room.remaining == Decimal(2000)
        assert not flows.room.over


def test_over_contributing_is_flagged(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            account="WS-TFSA",
            ticker=None,
            currency="CAD",
            amount="9000",
            price=None,
            units=None,
        )

        flows = _flows(
            Scope.TYPE,
            str(AccountType.TFSA),
            AccountType.TFSA,
            {AccountType.TFSA: {2025: Decimal(7000)}},
            2025,
        )

        assert flows.room is not None
        assert flows.room.remaining == Decimal(-2000)
        assert flows.room.over


def test_room_without_a_configured_limit_still_reports_what_was_used(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            account="WS-TFSA",
            ticker=None,
            currency="CAD",
            amount="5000",
            price=None,
            units=None,
        )

        flows = _flows(Scope.TYPE, str(AccountType.TFSA), AccountType.TFSA, {}, 2025)

        assert flows.room is not None
        assert flows.room.used == Decimal(5000)
        assert flows.room.limit is None
        assert flows.room.remaining is None
        assert not flows.room.over


@pytest.mark.parametrize(
    ("used", "limit", "ratio", "full"),
    [
        (Decimal(0), Decimal(7000), Decimal(0), False),
        (Decimal(3500), Decimal(7000), Decimal("0.5"), False),
        (Decimal(7000), Decimal(7000), Decimal(1), True),
        (Decimal("7000.001"), Decimal(7000), Decimal("7000.001") / 7000, True),
        (Decimal(9000), Decimal(7000), Decimal(9000) / 7000, False),
        (Decimal(5000), None, None, False),
        (Decimal(5000), Decimal(0), None, False),
    ],
)
def test_room_states_how_much_of_its_limit_is_used(
    used: Decimal,
    limit: Decimal | None,
    ratio: Decimal | None,
    full: bool,  # noqa: FBT001
) -> None:
    room = Room(AccountType.TFSA, 2025, used, limit)

    assert room.used_ratio == ratio
    assert room.full is full


def test_a_non_registered_pool_has_no_room_row(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            account="WS-PERSONAL",
            ticker=None,
            currency="CAD",
            amount="5000",
            price=None,
            units=None,
        )

        flows = _flows(
            Scope.TYPE,
            str(AccountType.NON_REGISTERED),
            AccountType.NON_REGISTERED,
            {},
            2025,
        )

        # An unbounded total would be meaningless, so the row is omitted.
        assert flows.room is None


def test_the_portfolio_scope_has_no_room_row(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            account="WS-TFSA",
            ticker=None,
            currency="CAD",
            amount="5000",
            price=None,
            units=None,
        )

        # Room belongs to one tax type; a whole-portfolio view spans several.
        assert _flows().room is None


def test_only_registered_types_bear_room() -> None:
    assert AccountType.TFSA in ROOM_BEARING_TYPES
    assert AccountType.RRSP in ROOM_BEARING_TYPES
    assert AccountType.NON_REGISTERED not in ROOM_BEARING_TYPES
    assert AccountType.MARGIN not in ROOM_BEARING_TYPES


def test_an_account_scope_sees_only_its_own_cash(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        for account, amount in (("IBKR-TFSA", "1000"), ("WS-TFSA", "2000")):
            seed_transaction(
                action="CONTRIBUTION",
                account=account,
                ticker=None,
                currency="CAD",
                amount=amount,
                price=None,
                units=None,
            )

        one = _flows(Scope.ACCOUNT, "IBKR-TFSA")
        pooled = _flows(Scope.TYPE, str(AccountType.TFSA))

        assert one.cash[Currency.CAD] == Decimal(1000)
        assert pooled.cash[Currency.CAD] == Decimal(3000)


def test_dividends_and_fees_are_carried_per_currency(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="DIVIDEND",
            ticker="AAA",
            currency="USD",
            amount="42",
            price=None,
            units=None,
        )
        seed_transaction(
            ticker="AAA",
            currency="USD",
            amount="-1000",
            units="10",
            fee="-4.95",
            date="2025-08-18",
        )

        flows = _flows()

        assert flows.dividends[Currency.USD] == Decimal(42)
        # Fees accumulate as the amount *charged*, sign-corrected per account:
        # brokers disagree on which sign means "charged", so the prevailing one
        # is detected and the running total is positive either way.
        assert flows.fees[Currency.USD] == Decimal("4.95")


def test_the_year_split_of_an_empty_frame_is_empty() -> None:
    assert contributions_by_type_year(pd.DataFrame()) == {}


def test_the_year_split_skips_an_unreadable_date() -> None:
    frame = pd.DataFrame(
        [
            {
                str(Column.Txn.ACTION): str(Action.CONTRIBUTION),
                str(Column.Txn.TXN_DATE): "not-a-date",
                str(Column.Txn.AMOUNT): 1000.0,
                "AcctType": str(AccountType.TFSA),
            },
            {
                str(Column.Txn.ACTION): str(Action.CONTRIBUTION),
                str(Column.Txn.TXN_DATE): "2025-01-02",
                str(Column.Txn.AMOUNT): 7000.0,
                "AcctType": str(AccountType.TFSA),
            },
        ],
    )

    # The database enforces the date format, so this is belt-and-braces: a bad
    # row is dropped rather than taking the whole report down with it.
    assert contributions_by_type_year(frame) == {
        AccountType.TFSA: {2025: Decimal(7000)},
    }


def test_a_registered_pool_with_no_contributions_has_no_room_row(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(account="WS-TFSA", ticker="AAA", amount="-1000", units="10")

        # Nothing was ever contributed, so there is no year to report against.
        flows = _flows(Scope.TYPE, str(AccountType.TFSA), AccountType.TFSA, {})

        assert flows.room is None


def test_an_empty_pool_reports_nothing_rather_than_zeros(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(ticker="AAA", amount="-1000", units="10")

        flows = _flows(Scope.ACCOUNT, "NOSUCHACCOUNT")

        # Absent, not zero: there is no such pool to report on.
        assert flows.cash == {}
        assert flows.contributions == {}
        assert flows.negative_currencies == ()


def test_a_withdrawal_and_a_transfer_reduce_deposits_differently(
    temp_ctx: TempContext,
) -> None:
    """A withdrawal leaves the folio; a transfer only changes which account.

    So a withdrawal comes off at face value, while a transfer takes only its
    proportional share. Both shrink the sending account, by different rules, and
    the folio total must see only the withdrawal.
    """
    with temp_ctx():
        _seed_grown_then_swept("QT-TFSA", "WS-TFSA")
        seed_transaction(
            action="WITHDRAWAL",
            account="QT-TFSA",
            ticker=None,
            currency="CAD",
            amount="-1000",
            price=None,
            units=None,
            date="2025-08-19",
            settle_date="2025-08-19",
        )

        sender = _flows(Scope.ACCOUNT, "QT-TFSA")
        folio = _flows()

        # 5,000 left after the sweep, less the 1,000 taken out at face value.
        assert sender.net_deposited[Currency.CAD] == Decimal(4000)
        # The folio only ever saw the contribution and the withdrawal.
        assert folio.net_deposited == {Currency.CAD: Decimal(9000)}


def test_a_transfer_out_of_an_emptied_account_moves_nothing(
    temp_ctx: TempContext,
) -> None:
    """An account with no deposits left has none to hand on.

    The second transfer finds a zero balance for the currency and must leave it
    alone rather than dividing by the pool again.
    """
    with temp_ctx():
        _seed_grown_then_swept("QT-TFSA", "WS-TFSA")
        # Sweep the remaining cash out too, then a third leg after it.
        for txn_id, (out_date, amount) in enumerate(
            (("2025-08-19", "-7500"), ("2025-08-20", "-3")),
        ):
            seed_transaction(
                action="TFR_OUT",
                account="QT-TFSA",
                ticker=None,
                currency="CAD",
                amount=amount,
                price=None,
                units=None,
                date=out_date,
                settle_date=out_date,
            )
            seed_transaction(
                action="TFR_IN",
                account="WS-TFSA",
                ticker=None,
                currency="CAD",
                amount=amount.lstrip("-"),
                price=None,
                units=None,
                date=out_date,
                settle_date=out_date,
            )
            del txn_id

        sender = _flows(Scope.ACCOUNT, "QT-TFSA")
        receiver = _flows(Scope.ACCOUNT, "WS-TFSA")

        assert sender.net_deposited[Currency.CAD] == Decimal(0)
        assert receiver.net_deposited[Currency.CAD] == Decimal(10000)


def test_an_in_leg_settling_before_its_out_leg_still_balances(
    temp_ctx: TempContext,
) -> None:
    """A broker can credit the receiving side before the sender releases.

    Pairing allows a settle-date window, so the cash walk can reach the in leg
    first. The sending side is settled then, while it still holds what is about
    to leave, rather than the arriving deposits being lost.
    """
    with temp_ctx():
        seed_fx(FX_DATES)
        seed_transaction(
            action="CONTRIBUTION",
            account="QT-TFSA",
            ticker=None,
            currency="CAD",
            amount="10000",
            price=None,
            units=None,
            date="2025-08-14",
            settle_date="2025-08-14",
        )
        # The in leg lands two days before the out leg it belongs to.
        seed_transaction(
            action="TFR_IN",
            account="WS-TFSA",
            ticker=None,
            currency="CAD",
            amount="4000",
            price=None,
            units=None,
            date="2025-08-18",
            settle_date="2025-08-18",
        )
        seed_transaction(
            action="TFR_OUT",
            account="QT-TFSA",
            ticker=None,
            currency="CAD",
            amount="-4000",
            price=None,
            units=None,
            date="2025-08-20",
            settle_date="2025-08-20",
        )

        sender = _flows(Scope.ACCOUNT, "QT-TFSA")
        receiver = _flows(Scope.ACCOUNT, "WS-TFSA")
        folio = _flows()

        assert sender.net_deposited[Currency.CAD] == Decimal(6000)
        assert receiver.net_deposited[Currency.CAD] == Decimal(4000)
        assert folio.net_deposited == {Currency.CAD: Decimal(10000)}


def test_income_arriving_after_an_account_empties_carries_no_deposits(
    temp_ctx: TempContext,
) -> None:
    """A trailing dividend is growth, and an emptied account has nothing left."""
    with temp_ctx():
        _seed_grown_then_swept("QT-TFSA", "WS-TFSA")
        # Sweep the rest, emptying the account of deposits entirely.
        seed_transaction(
            action="TFR_OUT",
            account="QT-TFSA",
            ticker=None,
            currency="CAD",
            amount="-7500",
            price=None,
            units=None,
            date="2025-08-19",
            settle_date="2025-08-19",
        )
        seed_transaction(
            action="TFR_IN",
            account="WS-TFSA",
            ticker=None,
            currency="CAD",
            amount="7500",
            price=None,
            units=None,
            date="2025-08-19",
            settle_date="2025-08-19",
        )
        # A trailing dividend, then it too is transferred across.
        seed_transaction(
            action="DIVIDEND",
            account="QT-TFSA",
            ticker="AAA",
            currency="CAD",
            amount="40",
            price=None,
            units=None,
            date="2025-08-20",
            settle_date="2025-08-20",
        )
        seed_transaction(
            action="TFR_OUT",
            account="QT-TFSA",
            ticker=None,
            currency="CAD",
            amount="-40",
            price=None,
            units=None,
            date="2025-08-20",
            settle_date="2025-08-20",
        )
        seed_transaction(
            action="TFR_IN",
            account="WS-TFSA",
            ticker=None,
            currency="CAD",
            amount="40",
            price=None,
            units=None,
            date="2025-08-20",
            settle_date="2025-08-20",
        )

        sender = _flows(Scope.ACCOUNT, "QT-TFSA")
        receiver = _flows(Scope.ACCOUNT, "WS-TFSA")

        assert sender.net_deposited[Currency.CAD] == Decimal(0)
        # Still exactly the 10,000 contributed: the dividend added nothing.
        assert receiver.net_deposited[Currency.CAD] == Decimal(10000)


def test_a_same_type_transfer_is_skipped_even_when_type_grain_is_tracked(
    temp_ctx: TempContext,
) -> None:
    """A cross-type transfer makes account-type grain live for the whole ledger.

    A same-type broker transfer must still be skipped there, because both its
    legs land in the one TFSA pool. Processing it would take from that pool and
    give straight back, which nets out only by accident and would read the pool
    mid-transfer if the legs settled apart.
    """
    with temp_ctx():
        seed_fx(FX_DATES)
        for account, amount in (("QT-TFSA", "10000"), ("WS-PERSONAL", "8000")):
            seed_transaction(
                action="CONTRIBUTION",
                account=account,
                ticker=None,
                currency="CAD",
                amount=amount,
                price=None,
                units=None,
                date="2025-08-14",
                settle_date="2025-08-14",
            )
        # Cross-type: non-registered into the TFSA. Tracks type grain.
        seed_transaction(
            action="TFR_OUT",
            account="WS-PERSONAL",
            ticker=None,
            currency="CAD",
            amount="-3000",
            price=None,
            units=None,
            date="2025-08-18",
            settle_date="2025-08-18",
        )
        seed_transaction(
            action="TFR_IN",
            account="QT-TFSA",
            ticker=None,
            currency="CAD",
            amount="3000",
            price=None,
            units=None,
            date="2025-08-18",
            settle_date="2025-08-18",
        )
        # Same-type: one TFSA broker to another, inside the pool above.
        seed_transaction(
            action="TFR_OUT",
            account="QT-TFSA",
            ticker=None,
            currency="CAD",
            amount="-5000",
            price=None,
            units=None,
            date="2025-08-19",
            settle_date="2025-08-19",
        )
        seed_transaction(
            action="TFR_IN",
            account="WS-TFSA",
            ticker=None,
            currency="CAD",
            amount="5000",
            price=None,
            units=None,
            date="2025-08-19",
            settle_date="2025-08-19",
        )

        tfsa = _flows(Scope.TYPE, str(AccountType.TFSA))
        folio = _flows()

        # The TFSA type gained only the 3,000 that crossed into it.
        assert tfsa.net_deposited[Currency.CAD] == Decimal(13000)
        assert folio.net_deposited == {Currency.CAD: Decimal(18000)}


def _seed_contributions_across_types() -> None:
    """Contribute to two room-bearing types across two calendar years."""
    seed_fx(FX_DATES)
    for account, amount, date in (
        ("WS-TFSA", "1000", "2025-08-14"),
        ("WS-TFSA", "500", "2025-08-15"),
        ("WS-RRSP", "2000", "2025-08-18"),
    ):
        seed_transaction(
            action="CONTRIBUTION",
            account=account,
            ticker=None,
            currency="CAD",
            amount=amount,
            price=None,
            units=None,
            date=date,
        )


def test_one_scan_keeps_each_types_contributions_apart(
    temp_ctx: TempContext,
) -> None:
    """Reading every type at once must not pool them together."""
    with temp_ctx():
        _seed_contributions_across_types()

        every = contributions_by_type_year(build().frame)

        assert every[AccountType.TFSA] == {2025: Decimal(1500)}
        assert every[AccountType.RRSP] == {2025: Decimal(2000)}


def test_room_reads_the_scan_it_was_handed(temp_ctx: TempContext) -> None:
    """The room line has to read the scan it was handed."""
    with temp_ctx():
        _seed_contributions_across_types()

        flows = _flows(
            Scope.TYPE,
            "TFSA",
            account_type=AccountType.TFSA,
            room={AccountType.TFSA: {2025: Decimal(7000)}},
        )

        assert flows.room is not None
        assert flows.room.used == Decimal(1500)
        assert flows.room.remaining == Decimal(5500)


def test_a_type_never_contributed_to_is_absent(temp_ctx: TempContext) -> None:
    with temp_ctx():
        _seed_contributions_across_types()

        every = contributions_by_type_year(build().frame)

        assert AccountType.FHSA not in every
