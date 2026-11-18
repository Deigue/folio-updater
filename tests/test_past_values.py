"""Tests for valuing every pool on a past date from its historical prices."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from domain import Currency, Grain
from domain.numeric import ZERO
from engine.accounts import FOLIO, Pool
from engine.ledger import open_ledger
from engine.past_values import UnpricedHolding, value_pools

from .helpers.seed import (
    SEPTEMBER_FX,
    seed_cash,
    seed_closes,
    seed_fx,
    seed_transaction,
)

if TYPE_CHECKING:
    from .helpers.context import TempContext

CAD = Currency.CAD
USD = Currency.USD
TFSA = "WS-TFSA"
TFSA_POOL = Pool(Grain.ACCOUNT, TFSA)


def _buy(
    ticker: str,
    day: str,
    *,
    account: str = TFSA,
    units: str = "10",
    price: str = "50",
) -> None:
    """Seed a CAD buy, its amount the units times the price."""
    seed_transaction(
        ticker=ticker,
        date=day,
        account=account,
        currency="CAD",
        amount=str(-Decimal(units) * Decimal(price)),
        price=price,
        units=units,
    )


def test_a_weekend_values_each_pool_at_fridays_close_plus_its_cash(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(SEPTEMBER_FX)
        seed_cash("CONTRIBUTION", amount="5000", account=TFSA, date="2025-09-02")
        _buy("RY.TO", "2025-09-03")
        seed_closes(
            "RY.TO",
            ("2025-09-11", "54"),
            ("2025-09-12", "55"),
            ("2025-09-15", "60"),
        )

        [valued] = value_pools(open_ledger(), [date(2025, 9, 14)])

    tfsa = valued.pools[TFSA_POOL]
    assert valued.on == date(2025, 9, 12)  # Sunday values at Friday's close
    assert [(held.symbol, held.units, held.price) for held in tfsa.holdings] == [
        ("RY.TO", Decimal(10), Decimal(55)),
    ]
    assert tfsa.holdings_market_value(CAD) == Decimal(550)
    assert tfsa.cash_in(CAD) == Decimal(4500)
    assert tfsa.market_value(CAD) == Decimal(5050)


def test_a_buy_on_the_valuation_date_moves_its_cash_before_it_settles(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(SEPTEMBER_FX)
        seed_cash(
            "CONTRIBUTION",
            amount="1000",
            currency="USD",
            account=TFSA,
            date="2025-09-02",
        )
        seed_transaction(
            ticker="SPY",
            date="2025-09-12",
            settle_date="2025-09-15",
            account=TFSA,
            amount="-600",
            price="60",
            units="10",
        )
        seed_closes("SPY", ("2025-09-12", "62"))

        [valued] = value_pools(open_ledger(), [date(2025, 9, 12)])

    tfsa = valued.pools[TFSA_POOL]
    # The units count from the trade, so the cash leaves with them, not on settling.
    assert tfsa.cash_in(USD) == Decimal(400)
    assert tfsa.holdings_market_value(CAD) == Decimal("868.0")  # 620 USD at 1.4
    assert tfsa.market_value(CAD) == Decimal("1428.0")


def test_cash_between_a_transfers_legs_stays_in_the_pool_holding_both_accounts(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(SEPTEMBER_FX)
        seed_cash("CONTRIBUTION", amount="2000", account=TFSA, date="2025-09-02")
        seed_cash("TFR_OUT", amount="-300", account=TFSA, date="2025-09-11")
        seed_cash("TFR_IN", amount="300", account="QT-TFSA", date="2025-09-16")

        [valued] = value_pools(open_ledger(), [date(2025, 9, 14)])

    cash = {pool: dated.cash_in(CAD) for pool, dated in valued.pools.items()}
    assert cash[TFSA_POOL] == Decimal(1700)
    assert Pool(Grain.ACCOUNT, "QT-TFSA") not in cash  # nothing has reached it yet
    assert cash[Pool(Grain.TYPE, "TFSA")] == Decimal(2000)
    assert cash[FOLIO] == Decimal(2000)
    rows = valued.rows()
    assert rows is not None
    assert rows.in_transit(CAD) == Decimal(300)


def _move_in_kind() -> None:
    """Buy 10 RY.TO in the TFSA, then move them to QT-TFSA from the 11th to the 16th."""
    _buy("RY.TO", "2025-09-03")
    for action, account, units, day in (
        ("TFR_OUT", TFSA, "-10", "2025-09-11"),
        ("TFR_IN", "QT-TFSA", "10", "2025-09-16"),
    ):
        seed_transaction(
            action=action,
            ticker="RY.TO",
            account=account,
            currency="CAD",
            amount=None,
            price=None,
            units=units,
            date=day,
        )


def test_units_between_a_transfers_legs_count_only_in_the_folio_and_its_type(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(SEPTEMBER_FX)
        _move_in_kind()
        seed_closes("RY.TO", ("2025-09-12", "55"))

        [valued] = value_pools(open_ledger(), [date(2025, 9, 12)])

    held = {
        pool: dated.holdings_market_value(CAD) for pool, dated in valued.pools.items()
    }
    assert held[TFSA_POOL] == ZERO
    assert held[Pool(Grain.TYPE, "TFSA")] == Decimal(550)
    rows = valued.rows()
    assert rows is not None
    assert rows.in_transit(CAD) == Decimal(550)
    assert rows.line_in_transit(CAD) == Decimal(550)
    moving = rows.holding_in_transit("RY.TO")
    assert moving is not None
    assert (moving.units, moving.price) == (Decimal(10), Decimal(55))


def test_unpriced_units_in_transit_are_named_as_in_transit(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(SEPTEMBER_FX)
        _move_in_kind()

        [valued] = value_pools(open_ledger(), [date(2025, 9, 12)])

    assert valued.unpriced() == [UnpricedHolding("RY.TO", Decimal(10), None)]


def test_a_holding_with_no_price_is_unpriced_and_left_out_of_the_value(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(SEPTEMBER_FX)
        _buy("RY.TO", "2025-09-03")
        _buy("XEQT.TO", "2025-09-03", units="4", price="30")
        seed_closes("RY.TO", ("2025-09-12", "55"))

        [valued] = value_pools(open_ledger(), [date(2025, 9, 12)])

    tfsa = valued.pools[TFSA_POOL]
    assert [(held.symbol, held.units) for held in tfsa.unpriced] == [
        ("XEQT.TO", Decimal(4)),
    ]
    assert tfsa.holdings_market_value(CAD) == Decimal(550)


def test_a_usd_line_with_no_prices_is_priced_from_its_cad_twin(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(SEPTEMBER_FX)
        seed_transaction(
            ticker="DLR.U.TO",
            date="2025-09-03",
            account=TFSA,
            amount="-98",
            price="9.8",
            units="10",
        )
        seed_closes("DLR.TO", ("2025-09-12", "14"))

        [valued] = value_pools(open_ledger(), [date(2025, 9, 12)])

    [held] = valued.pools[TFSA_POOL].holdings
    assert (held.symbol, held.price, held.twin) == ("DLR.U.TO", Decimal(10), "DLR.TO")
    assert valued.pools[TFSA_POOL].holdings_market_value(USD) == Decimal(100)


def test_a_day_before_the_first_transaction_values_no_pool(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(SEPTEMBER_FX)
        seed_cash("CONTRIBUTION", amount="2000", account=TFSA, date="2025-09-15")

        [valued] = value_pools(open_ledger(), [date(2025, 9, 12)])

    assert valued.pools == {}
    assert valued.rows() is None
