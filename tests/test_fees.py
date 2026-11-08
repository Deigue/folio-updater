"""An account's fee rules: where a commission sits, and how they are found."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from domain import Action, FeeConvention, FeeSign
from engine.fees import FeeRules, TradeCash

from .helpers.replay import make_row

if TYPE_CHECKING:
    import pytest

    from engine.types import TxnRow

D = Decimal

EXCLUDED_NEGATIVE = FeeRules(FeeConvention.EXCLUDED, FeeSign.NEGATIVE)
INCLUDED_NEGATIVE = FeeRules(FeeConvention.INCLUDED, FeeSign.NEGATIVE)
EXCLUDED_POSITIVE = FeeRules(FeeConvention.EXCLUDED, FeeSign.POSITIVE)


def _buy(fee: str, amount: str, units: str = "100", price: str = "10") -> TxnRow:
    return make_row(
        1,
        "2024-01-02",
        Action.BUY,
        amount=amount,
        units=units,
        price=price,
        fee=fee,
    )


def _sell(fee: str, amount: str) -> TxnRow:
    return make_row(
        2,
        "2024-01-03",
        Action.SELL,
        amount=amount,
        units="-100",
        price="10",
        fee=fee,
    )


# --- where the commission sits ---------------------------------------------------


def test_an_excluded_fee_is_added_to_a_buy_and_taken_from_a_sale() -> None:
    buy = EXCLUDED_NEGATIVE.trade_cash(_buy(fee="-2", amount="-1000"))
    sell = EXCLUDED_NEGATIVE.trade_cash(_sell(fee="-2", amount="1000"))
    assert buy == TradeCash(cost=D(1002), proceeds=D(998), commission=D(2))
    assert sell.proceeds == D(998)


def test_an_included_fee_is_already_in_the_amount() -> None:
    buy = INCLUDED_NEGATIVE.trade_cash(_buy(fee="-4.95", amount="-1004.95"))
    sell = INCLUDED_NEGATIVE.trade_cash(_sell(fee="-4.95", amount="995.05"))
    assert buy.cost == D("1004.95")
    assert sell.proceeds == D("995.05")
    assert buy.commission == sell.commission == D("4.95")


def test_the_other_sign_is_a_rebate() -> None:
    """On an account that writes charges negative, a positive fee is money back."""
    rules = EXCLUDED_NEGATIVE
    assert rules.commission(_buy(fee="0.35", amount="-1000")) == D("-0.35")
    assert rules.trade_cash(_buy(fee="0.35", amount="-1000")).cost == D("999.65")


def test_a_positive_sign_account_reads_the_fee_as_stored() -> None:
    row = _buy(fee="4.95", amount="-1000")
    assert EXCLUDED_POSITIVE.commission(row) == D("4.95")


def test_the_conventions_agree_when_there_is_no_fee() -> None:
    row = _buy(fee="0", amount="-1000")
    included = FeeRules(FeeConvention.INCLUDED, FeeSign.POSITIVE).trade_cash(row)
    excluded = EXCLUDED_POSITIVE.trade_cash(row)
    assert included.cost == excluded.cost == D(1000)


# --- does a trade add up ---------------------------------------------------------


def test_a_trade_reconciles_when_the_fee_closes_the_gap_either_way() -> None:
    rules = EXCLUDED_NEGATIVE
    assert rules.reconciles(_buy(fee="-1", amount="-1000"))
    assert rules.reconciles(_buy(fee="-4.95", amount="-1004.95"))


def test_a_trade_that_matches_neither_convention_does_not_reconcile() -> None:
    assert not EXCLUDED_NEGATIVE.reconciles(_buy(fee="-5", amount="-1234.56"))


def test_price_rounded_to_cents_still_reconciles() -> None:
    """QuestTrade rounds Price to 2dp, so Price*Units is off by half a cent a share."""
    row = _buy(fee="-4.95", amount="-1002.45", units="510", price="1.96")
    assert INCLUDED_NEGATIVE.reconciles(row)


# --- finding the rules from an account's trades ----------------------------------


def _ibkr_buys(fee: str) -> list[TxnRow]:
    """Two trades whose Amount is gross of the commission."""
    return [_buy(fee=fee, amount="-1000"), _buy(fee=fee, amount="-2000", units="200")]


def _questrade_buys(fee: str) -> list[TxnRow]:
    """Two trades whose Amount already contains the commission."""
    return [
        _buy(fee=fee, amount="-1004.95"),
        _buy(fee=fee, amount="-2004.95", units="200"),
    ]


def test_negative_charges_are_detected_with_the_convention() -> None:
    ibkr = FeeRules().resolve("IBKR-PERSONAL", _ibkr_buys("-1"))
    questrade = FeeRules().resolve("QT-TFSA", _questrade_buys("-4.95"))
    assert ibkr == EXCLUDED_NEGATIVE
    assert questrade == INCLUDED_NEGATIVE


def test_an_account_that_writes_charges_positive_is_detected() -> None:
    rules = FeeRules().resolve("HAND-TFSA", _questrade_buys("4.95"))
    assert rules == FeeRules(FeeConvention.INCLUDED, FeeSign.POSITIVE)


def test_a_minority_sign_does_not_flip_the_account() -> None:
    """IBKR rebates part of a commission, so a positive fee turns up now and then."""
    rows = [
        *_ibkr_buys("-1"),
        _buy(fee="-1", amount="-1000"),
        _buy(fee="0.5", amount="-1000"),
    ]
    assert FeeRules().resolve("IBKR-PERSONAL", rows).sign is FeeSign.NEGATIVE


def test_an_account_with_no_fees_defaults_to_excluded_and_positive() -> None:
    """What a hand-entered `--fee 4.95` means, and moot when every fee is zero."""
    rows = [_buy(fee="0", amount="-1000")]
    assert FeeRules().resolve("WS-PERSONAL", rows) == EXCLUDED_POSITIVE


def test_a_pinned_setting_skips_detection_for_that_setting_only() -> None:
    """A pinned `included` beats rows that look excluded; the sign is still read."""
    pinned = FeeRules(convention=FeeConvention.INCLUDED)
    resolved = pinned.resolve("QT-TFSA", _ibkr_buys("-1"))
    assert resolved == INCLUDED_NEGATIVE


def test_a_fully_pinned_account_ignores_its_rows() -> None:
    rules = FeeRules(FeeConvention.EXCLUDED, FeeSign.POSITIVE)
    assert rules.resolve("IBKR-PERSONAL", _ibkr_buys("-1")) is rules


def test_a_near_even_split_warns_and_still_decides(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Four votes for excluded against three for included is 57%, under the 60% bar.
    rows = [_buy(fee="-5", amount="-1000")] * 4 + [_buy(fee="-5", amount="-1005")] * 3
    with caplog.at_level("WARNING"):
        resolved = FeeRules().resolve("IBKR-PERSONAL", rows)
    assert resolved.convention is FeeConvention.EXCLUDED
    assert "splits near evenly" in caplog.text
