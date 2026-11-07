"""Tests for a pool's identity and the figures it is valued with."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from domain import Currency, DiagnosticCode, Grain
from engine.ledger import open_ledger
from engine.pools import (
    FOLIO,
    Conversion,
    Converted,
    FolioValuation,
    Pool,
    Return,
    TotalReturnHidden,
    UnitsSuspect,
    Unpriced,
)

from .helpers.seed import TWO_TYPES_FX, seed_cash, seed_fx, seed_transaction

if TYPE_CHECKING:
    from engine.holdings import DisplayCurrency
    from engine.pools import ValuedPool

    from .helpers.context import TempContext


@pytest.mark.parametrize(
    ("pool", "label"),
    [
        (Pool(Grain.ACCOUNT, "WS-TFSA"), "WS-TFSA"),
        (Pool(Grain.TYPE, "NON_REGISTERED"), "NON-REGISTERED"),
        (FOLIO, "Portfolio"),
    ],
    ids=["account", "type", "folio"],
)
def test_a_pool_names_itself_from_its_grain(pool: Pool, label: str) -> None:
    assert pool.label == label


def _usd_only(currency: DisplayCurrency = "native") -> ValuedPool:
    """Fund a TFSA with 10,000 CAD and buy 10 TESTTKR at 100 USD.

    At the mocked 200 USD and 1.25 USDCAD the holding is worth 2,500 CAD on a
    1,250 CAD cost, so it has earned 1,250 CAD: 12.5% of its net deposits.
    """
    seed_fx(TWO_TYPES_FX)
    seed_cash("CONTRIBUTION", amount="10000", account="WS-TFSA", date="2025-08-14")
    seed_transaction(
        account="WS-TFSA",
        ticker="TESTTKR",
        currency="USD",
        amount="-1000",
        price="100",
        units="10",
    )
    valuation = FolioValuation.build(open_ledger(), currency=currency)
    return valuation.value(Pool(Grain.ACCOUNT, "WS-TFSA"))


def test_a_usd_only_pools_subtotal_is_its_return_on_net_deposits(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        valued = _usd_only()

        (group,) = valued.holdings.by_currency
        promoted = valued.subtotal_return(group)

        assert promoted.ratio == Decimal("0.125")
        assert promoted.gain == Decimal(1250)


def test_a_usd_only_pool_discloses_its_return_as_converted(
    temp_ctx: TempContext,
) -> None:
    """Holdings and cash stay in USD; only the promoted `Total%` borrowed CAD."""
    with temp_ctx():
        valued = _usd_only()

        assert valued.disclosures == (
            Converted(
                Conversion.TOTAL_RETURN,
                Currency.CAD,
                Decimal("1.25"),
                "2025-08-20",
            ),
        )


def _mixed(
    currency: DisplayCurrency = "native",
    funding: tuple[str, ...] = ("CAD", "USD"),
) -> ValuedPool:
    """Fund a TFSA in `funding`'s currencies and hold one security in each.

    10 TESTTKR bought at 100 USD trade at 200 USD (1,250 CAD gained at 1.25), and
    10 CADCO.TO bought at 200 CAD trade at 20 CAD (1,800 CAD lost), so the pool
    is down 550 CAD on 10,000 CAD of net deposits.
    """
    seed_fx(TWO_TYPES_FX)
    for funded in funding:
        seed_cash(
            "CONTRIBUTION",
            amount="10000",
            currency=funded,
            account="WS-TFSA",
            date="2025-08-14",
        )
    seed_transaction(
        account="WS-TFSA",
        ticker="TESTTKR",
        currency="USD",
        amount="-1000",
        price="100",
        units="10",
    )
    seed_transaction(
        account="WS-TFSA",
        ticker="CADCO.TO",
        currency="CAD",
        amount="-2000",
        price="200",
        units="10",
    )
    valuation = FolioValuation.build(open_ledger(), currency=currency)
    return valuation.value(Pool(Grain.ACCOUNT, "WS-TFSA"))


def test_a_mixed_pool_measures_subtotals_on_book_and_its_total_on_deposits(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        valued = _mixed()

        usd = next(g for g in valued.holdings.by_currency if g.currency is Currency.USD)

        assert valued.subtotal_return(usd) == Return(Decimal(1), Decimal(1000))
        assert valued.total_return == Return(Decimal("-0.055"), Decimal(-550))


def test_hiding_part_of_a_pool_says_why_its_return_is_blank(
    temp_ctx: TempContext,
) -> None:
    """`-c USD` drops the CAD holding, so no ratio on net deposits describes it.

    Nothing is converted either: the CAD holding is dropped, not converted.
    """
    with temp_ctx():
        valued = _mixed(Currency.USD)

        (usd,) = valued.holdings.by_currency

        assert valued.subtotal_return(usd) == Return()
        assert valued.disclosures == (TotalReturnHidden(1, Currency.USD),)


def test_a_pool_with_no_cad_deposits_has_no_hidden_return_to_explain(
    temp_ctx: TempContext,
) -> None:
    """Funded only in USD, its `Total%` is blank whatever the filter hides."""
    with temp_ctx():
        valued = _mixed(Currency.USD, funding=("USD",))

        assert valued.disclosures == ()


def test_a_holding_with_no_quote_is_named_as_left_out_of_the_totals(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(TWO_TYPES_FX)
        seed_cash("CONTRIBUTION", amount="5000", account="WS-TFSA", date="2025-08-14")
        # The mocked provider knows nothing about MSFT.
        seed_transaction(account="WS-TFSA", ticker="MSFT", amount="-400", units="2")
        valuation = FolioValuation.build(open_ledger(), currency="native")

        valued = valuation.value(Pool(Grain.ACCOUNT, "WS-TFSA"))

        assert Unpriced(("MSFT",)) in valued.disclosures


def test_an_oversold_holdings_units_are_suspect_and_disclosed(
    temp_ctx: TempContext,
) -> None:
    with temp_ctx():
        seed_fx(TWO_TYPES_FX)
        seed_cash("CONTRIBUTION", amount="10000", date="2025-08-14")
        seed_transaction(ticker="TESTTKR", amount="-1000", price="100", units="10")
        # Selling more than was ever held drives units negative.
        seed_transaction(
            action="SELL",
            ticker="TESTTKR",
            amount="2500",
            price="125",
            units="20",
            date="2025-08-18",
        )
        valuation = FolioValuation.build(open_ledger(), currency="native")

        valued = valuation.value(FOLIO)

        (holding,) = valued.holdings.holdings
        assert holding.units_suspect
        assert UnitsSuspect((DiagnosticCode.OVERSELL,)) in valued.disclosures


def test_a_pool_valued_with_no_rate_held_states_no_conversion(
    temp_ctx: TempContext,
) -> None:
    """Offline, a CAD-only folio may never have fetched a rate, so it states none."""
    with temp_ctx():
        seed_cash("CONTRIBUTION", amount="5000", account="WS-TFSA", date="2025-08-14")
        seed_transaction(
            account="WS-TFSA",
            ticker="CADCO.TO",
            currency="CAD",
            amount="-2000",
            price="200",
            units="10",
        )
        ledger = open_ledger(offline=True)
        valuation = FolioValuation.build(ledger, currency=Currency.CAD, offline=True)

        valued = valuation.value(Pool(Grain.ACCOUNT, "WS-TFSA"))

        assert not any(isinstance(found, Converted) for found in valued.disclosures)
