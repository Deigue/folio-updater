"""Tests for the folio health checks and the `folio check` command.

The false-positive cases here matter as much as the positive ones. A check that
fires on a correctly-recorded folio is something that should not be ignored.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from cli.commands.check import _print_findings
from cli.main import app
from cli.test_console import capture_output
from engine.cache import load_or_build
from engine.checks import (
    CHECK_SLUGS,
    CheckFinding,
    CheckResult,
    ChecksConfig,
    UnknownCheckError,
    run_checks,
    validate_slugs,
)
from engine.events import TxnRow
from engine.fx_rates import FxRates
from engine.replay import ReplayConfig, detect_fee_signs, replay
from services.symbols import SymbolResolver
from utils.constants import (
    AccountType,
    Action,
    CheckStatus,
    Currency,
    FeeConvention,
    WarningCode,
)

from .helpers.cli import assert_in_output, assert_not_in_output, run_cli_with_config
from .helpers.seed import seed_fx, seed_transaction

if TYPE_CHECKING:
    from engine.events import ReplayResult

    from .test_types import TempContext

D = Decimal

FLAT_FX = FxRates(("2020-01-01",), (D("1.35"),))

# Every account these tests use, so a replay never has to guess a type.
ACCOUNT_TYPES = {
    "IBKR-PERSONAL": AccountType.NON_REGISTERED,
    "WS-PERSONAL": AccountType.NON_REGISTERED,
    "IBKR-TFSA": AccountType.TFSA,
    "WS-TFSA": AccountType.TFSA,
    "QT-TFSA": AccountType.TFSA,
    "IBKR-RRSP": AccountType.RRSP,
    "QT-RRSP": AccountType.RRSP,
}


class StubConfig:
    """The three settings `run_checks` reads, and nothing else."""

    def __init__(
        self,
        disabled: list[str] | None = None,
        tickers: list[str] | None = None,
        accounts: list[str] | None = None,
    ) -> None:
        """Record the three suppression lists."""
        self.checks_disabled = disabled or []
        self.checks_ignore_tickers = [name.upper() for name in tickers or []]
        self.checks_ignore_accounts = [name.upper() for name in accounts or []]


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
    ticker: str | None = "TEST",
    account: str = "IBKR-PERSONAL",
    settle_date: str | None = None,
) -> TxnRow:
    """Build one transaction with every money column already Decimal."""
    return TxnRow(
        txn_id=txn_id,
        txn_date=date,
        settle_date=settle_date or date,
        action=action,
        amount=D(amount),
        currency=Currency(currency),
        price=D(price),
        units=D(units),
        fee=D(fee),
        ticker=ticker,
        account=account,
    )


def replay_of(rows: list[TxnRow]) -> ReplayResult:
    """Replay rows with explicit account facts, so no config is involved."""
    accounts = {row.account for row in rows}
    cfg = ReplayConfig(
        account_types={
            name: ACCOUNT_TYPES.get(name, AccountType.NON_REGISTERED)
            for name in accounts
        },
        fee_conventions=dict.fromkeys(accounts, FeeConvention.AUTO),
        symbols=SymbolResolver([]),
        fee_signs=detect_fee_signs(rows),
    )
    return replay(rows, FLAT_FX, cfg)


def check(
    rows: list[TxnRow],
    slug: str,
    config: ChecksConfig | None = None,
) -> CheckResult:
    """Run one named check over a set of rows."""
    results = run_checks(replay_of(rows), config or StubConfig())
    return next(result for result in results if result.slug == slug)


def details(result: CheckResult) -> str:
    """Every finding's text, joined, for substring assertions."""
    return " | ".join(
        f"{finding.subject} {finding.detail}" for finding in result.findings
    )


# --- a folio with nothing wrong -----------------------------------------------


def clean_folio() -> list[TxnRow]:
    """Build a small folio that every check should pass."""
    return [
        make_row(1, "2024-01-02", Action.CONTRIBUTION, amount="10000", ticker=None),
        make_row(
            2,
            "2024-01-03",
            Action.BUY,
            amount="-1000",
            units="100",
            price="10",
            ticker="RY.TO",
            settle_date="2024-01-05",
        ),
        make_row(
            3,
            "2024-02-01",
            Action.DIVIDEND,
            amount="25",
            ticker="RY.TO",
        ),
        make_row(
            4,
            "2024-03-01",
            Action.SELL,
            amount="600",
            units="-50",
            price="12",
            ticker="RY.TO",
            settle_date="2024-03-05",
        ),
    ]


def test_a_correct_folio_passes_every_check() -> None:
    results = run_checks(replay_of(clean_folio()), StubConfig())
    failing = [result.slug for result in results if result.status is not CheckStatus.OK]
    assert failing == []
    assert {result.slug for result in results} == set(CHECK_SLUGS)


def test_every_check_reports_a_summary_even_when_it_passes() -> None:
    for result in run_checks(replay_of(clean_folio()), StubConfig()):
        assert result.summary
        assert result.findings == ()


# --- account types ------------------------------------------------------------


def test_an_unrecognised_account_name_is_reported() -> None:
    rows = [make_row(1, "2024-01-02", Action.BUY, amount="-10", units="1", price="10")]
    cfg = ReplayConfig(
        account_types={"IBKR-PERSONAL": AccountType.UNKNOWN},
        fee_conventions={"IBKR-PERSONAL": FeeConvention.AUTO},
        symbols=SymbolResolver([]),
    )
    results = run_checks(replay(rows, FLAT_FX, cfg), StubConfig())
    result = next(item for item in results if item.slug == "account-types")
    assert result.status is CheckStatus.FAIL
    assert "accounts.map" in details(result)


def test_inferred_account_types_pass() -> None:
    assert check(clean_folio(), "account-types").status is CheckStatus.OK


# --- unit balances ------------------------------------------------------------


def oversold_in_one_account() -> list[TxnRow]:
    """Build a TFSA whose units are right in total but wrong per account."""
    return [
        make_row(
            1,
            "2023-01-06",
            Action.BUY,
            amount="-2200",
            units="100",
            price="22",
            ticker="FTS.TO",
            account="QT-TFSA",
        ),
        make_row(
            2,
            "2023-02-02",
            Action.BUY,
            amount="-92",
            units="4",
            price="23",
            ticker="FTS.TO",
            account="WS-TFSA",
        ),
        make_row(
            3,
            "2023-09-18",
            Action.SELL,
            amount="500",
            units="-22",
            price="22.72",
            ticker="FTS.TO",
            account="WS-TFSA",
        ),
    ]


def test_an_oversell_is_reported_against_the_security() -> None:
    result = check(oversold_in_one_account(), "unit-balances")
    assert result.status is CheckStatus.FAIL
    assert result.findings[0].subject == "FTS.TO"
    assert "WS-TFSA sold 22 units on 2023-09-18" in details(result)
    assert "18 units are unaccounted for" in details(result)


def test_an_account_short_but_a_type_balanced_points_at_a_transfer() -> None:
    result = check(oversold_in_one_account(), "unit-balances")
    assert "The TFSA total is correct" in details(result)
    assert "moved from another account without being recorded" in details(result)


def test_units_missing_from_the_whole_folio_say_so() -> None:
    rows = [
        make_row(
            1,
            "2023-09-18",
            Action.SELL,
            amount="500",
            units="-22",
            price="22.72",
            ticker="FTS.TO",
            account="WS-TFSA",
        ),
    ]
    assert "missing from the folio entirely" in details(check(rows, "unit-balances"))


def test_units_missing_across_account_types_say_so() -> None:
    rows = [
        make_row(
            1,
            "2023-01-06",
            Action.BUY,
            amount="-2200",
            units="100",
            price="22",
            ticker="FTS.TO",
            account="IBKR-RRSP",
        ),
        make_row(
            2,
            "2023-09-18",
            Action.SELL,
            amount="500",
            units="-22",
            price="22.72",
            ticker="FTS.TO",
            account="WS-TFSA",
        ),
    ]
    assert "transfer between account types" in details(check(rows, "unit-balances"))


def test_a_same_day_buy_then_sell_is_not_an_oversell() -> None:
    rows = [
        make_row(
            1,
            "2024-01-02",
            Action.SELL,
            amount="600",
            units="-50",
            price="12",
            ticker="RY.TO",
        ),
        make_row(
            2,
            "2024-01-02",
            Action.BUY,
            amount="-1000",
            units="100",
            price="10",
            ticker="RY.TO",
        ),
    ]
    assert check(rows, "unit-balances").status is CheckStatus.OK


def test_a_position_left_negative_by_a_reported_sale_is_not_reported_twice() -> None:
    """The oversell and the negative ending are one defect, not two."""
    result = check(oversold_in_one_account(), "unit-balances")
    assert len(result.findings) == 1


def test_a_position_left_negative_with_no_sale_is_reported() -> None:
    rows = [
        make_row(
            1,
            "2025-02-10",
            Action.TFR_OUT,
            units="-40",
            ticker="VFV.TO",
            account="WS-TFSA",
        ),
    ]
    result = check(rows, "unit-balances")
    assert result.status is CheckStatus.FAIL
    assert "ends holding -40 units" in details(result)


# --- cash balances ------------------------------------------------------------


def test_the_first_date_cash_goes_negative_is_reported() -> None:
    rows = [
        make_row(
            1,
            "2024-06-03",
            Action.BUY,
            amount="-500",
            units="5",
            price="100",
            ticker="XEQT.TO",
            account="WS-PERSONAL",
        ),
        make_row(
            2,
            "2024-07-03",
            Action.BUY,
            amount="-900",
            units="9",
            price="100",
            ticker="XEQT.TO",
            account="WS-PERSONAL",
        ),
    ]
    result = check(rows, "cash-balances")
    assert result.status is CheckStatus.FAIL
    # The first crossing, not the deepest: the first is the lead.
    assert "2024-06-03" in details(result)
    assert "2024-07-03" not in details(result)


def test_a_balance_that_never_recovers_is_called_one_past_error() -> None:
    rows = [
        make_row(
            1,
            "2024-06-03",
            Action.BUY,
            amount="-500",
            units="5",
            price="100",
            ticker="XEQT.TO",
            account="WS-PERSONAL",
        ),
    ]
    assert "sat there unchanged ever since" in details(check(rows, "cash-balances"))


def test_a_buy_funded_the_same_day_never_goes_negative() -> None:
    """Cash follows the settle date, so same-day funding lands before the buy."""
    rows = [
        make_row(1, "2024-06-03", Action.CONTRIBUTION, amount="500", ticker=None),
        make_row(
            2,
            "2024-06-03",
            Action.BUY,
            amount="-500",
            units="5",
            price="100",
            ticker="XEQT.TO",
        ),
    ]
    assert check(rows, "cash-balances").status is CheckStatus.OK


def test_the_cash_finding_names_the_account_and_its_currency() -> None:
    rows = [
        make_row(
            1,
            "2024-06-03",
            Action.BUY,
            amount="-500",
            units="5",
            price="100",
            ticker="MSFT",
            currency="USD",
        ),
    ]
    assert check(rows, "cash-balances").findings[0].subject == "IBKR-PERSONAL USD"


# --- split coverage -----------------------------------------------------------


def split_missing_from_one_account() -> list[TxnRow]:
    """Two accounts hold SCHD, but only one records the split."""
    return [
        make_row(
            1,
            "2024-01-04",
            Action.BUY,
            amount="-8000",
            units="100",
            price="80",
            ticker="SCHD",
            account="IBKR-RRSP",
            currency="USD",
        ),
        make_row(
            2,
            "2024-01-06",
            Action.BUY,
            amount="-4000",
            units="50",
            price="80",
            ticker="SCHD",
            account="IBKR-TFSA",
            currency="USD",
        ),
        make_row(
            3,
            "2024-10-10",
            Action.SPLIT,
            units="3",
            price="1",
            ticker="SCHD",
            account="IBKR-RRSP",
            currency="USD",
        ),
    ]


def test_a_split_missing_from_a_holding_account_is_reported() -> None:
    result = check(split_missing_from_one_account(), "split-coverage")
    assert result.status is CheckStatus.FAIL
    assert "IBKR-TFSA held it that day" in details(result)
    assert "pre-split count" in details(result)


def test_a_split_recorded_for_every_holder_passes() -> None:
    rows = [
        *split_missing_from_one_account(),
        make_row(
            4,
            "2024-10-10",
            Action.SPLIT,
            units="3",
            price="1",
            ticker="SCHD",
            account="IBKR-TFSA",
            currency="USD",
        ),
    ]
    result = check(rows, "split-coverage")
    assert result.status is CheckStatus.OK
    assert result.findings == ()


def test_the_same_split_recorded_twice_is_reported() -> None:
    rows = [
        *split_missing_from_one_account(),
        make_row(
            4,
            "2024-10-10",
            Action.SPLIT,
            units="3",
            price="1",
            ticker="SCHD",
            account="IBKR-RRSP",
            currency="USD",
        ),
        make_row(
            5,
            "2024-10-10",
            Action.SPLIT,
            units="3",
            price="1",
            ticker="SCHD",
            account="IBKR-TFSA",
            currency="USD",
        ),
    ]
    result = check(rows, "split-coverage")
    assert "is split twice" in details(result)


def test_an_account_that_sold_out_before_a_split_is_not_expected_to_have_one() -> None:
    rows = [
        *split_missing_from_one_account(),
        make_row(
            4,
            "2024-05-01",
            Action.SELL,
            amount="4200",
            units="-50",
            price="84",
            ticker="SCHD",
            account="IBKR-TFSA",
            currency="USD",
        ),
    ]
    assert check(rows, "split-coverage").status is CheckStatus.OK


# --- conversion arithmetic ----------------------------------------------------


def test_a_conversion_that_does_not_add_up_shows_all_three_numbers() -> None:
    rows = [
        make_row(
            1,
            "2024-05-31",
            Action.FXT,
            amount="-13000",
            units="9541.98",
            price="1.3624",
            ticker=None,
            account="IBKR-RRSP",
        ),
    ]
    result = check(rows, "conversion-arithmetic")
    assert result.status is CheckStatus.FAIL
    text = details(result)
    # The recorded figure and the implied figure, side by side and comparable.
    assert "-13,000.00 CAD" in text
    assert "9,541.98 x 1.3624" in text
    assert "-12,999.99" in text


def test_a_conversion_that_adds_up_passes() -> None:
    rows = [
        make_row(
            1,
            "2024-05-31",
            Action.FXT,
            amount="-13000",
            units="10000",
            price="1.3",
            ticker=None,
            account="IBKR-RRSP",
        ),
    ]
    assert check(rows, "conversion-arithmetic").status is CheckStatus.OK


def test_a_two_leg_conversion_carries_no_arithmetic_to_check() -> None:
    """Questrade rows have no Price or Units, so there is nothing to reconcile."""
    rows = [
        make_row(1, "2023-03-01", Action.FXT, amount="-1000", ticker=None),
        make_row(
            2,
            "2023-03-01",
            Action.FXT,
            amount="740",
            currency="USD",
            ticker=None,
        ),
    ]
    assert check(rows, "conversion-arithmetic").status is CheckStatus.OK


# --- currency consistency -----------------------------------------------------


def test_a_security_booked_in_two_currencies_is_reported() -> None:
    rows = [
        make_row(
            1,
            "2024-03-01",
            Action.BUY,
            amount="-1000",
            units="10",
            price="100",
            ticker="AAPL",
            currency="USD",
        ),
        make_row(
            2,
            "2024-04-01",
            Action.BUY,
            amount="-1100",
            units="10",
            price="110",
            ticker="AAPL",
            currency="CAD",
        ),
    ]
    result = check(rows, "currency-consistency")
    assert result.status is CheckStatus.FAIL
    assert result.findings[0].subject == "AAPL"
    assert "1 row of CAD" in details(result)
    assert "1 row of USD" in details(result)


def test_a_split_row_does_not_invent_a_currency_conflict() -> None:
    """A SPLIT moves no money, so its `$` is not evidence of anything."""
    rows = [
        make_row(
            1,
            "2024-03-01",
            Action.BUY,
            amount="-1000",
            units="10",
            price="100",
            ticker="AAPL",
            currency="USD",
        ),
        make_row(
            2,
            "2024-10-10",
            Action.SPLIT,
            units="4",
            price="1",
            ticker="AAPL",
            currency="CAD",
        ),
    ]
    assert check(rows, "currency-consistency").status is CheckStatus.OK


# --- income sanity ------------------------------------------------------------


def test_a_dividend_with_no_position_is_reported() -> None:
    rows = [
        make_row(1, "2024-02-01", Action.DIVIDEND, amount="25", ticker="RY.TO"),
    ]
    result = check(rows, "income-sanity")
    assert result.status is CheckStatus.WARN
    assert "while holding none of it" in details(result)


def test_a_dividend_just_after_a_sale_is_not_reported() -> None:
    """A distribution is earned on the ex-date and paid weeks later."""
    rows = [
        *clean_folio(),
        make_row(5, "2024-03-20", Action.DIVIDEND, amount="25", ticker="RY.TO"),
    ]
    assert check(rows, "income-sanity").status is CheckStatus.OK


def test_return_of_capital_past_the_cost_base_is_reported() -> None:
    rows = [
        make_row(
            1,
            "2024-01-03",
            Action.BUY,
            amount="-100",
            units="10",
            price="10",
            ticker="ZAG.TO",
        ),
        make_row(2, "2024-06-01", Action.ROC, amount="400", ticker="ZAG.TO"),
    ]
    result = check(rows, "income-sanity")
    assert "past what the position cost" in details(result)


# --- settlement dates ---------------------------------------------------------


def test_a_trade_settling_before_it_was_made_is_reported() -> None:
    rows = [
        make_row(
            1,
            "2024-03-05",
            Action.BUY,
            amount="-1000",
            units="100",
            price="10",
            ticker="RY.TO",
            settle_date="2024-03-01",
        ),
    ]
    result = check(rows, "settlement-dates")
    assert result.status is CheckStatus.WARN
    assert "before the 2024-03-05 trade" in details(result)


def trades_with_lags(lags: list[int]) -> list[TxnRow]:
    """Build one BUY a week apart for each lag, lagged in calendar days."""
    monday = date(2024, 1, 1)
    rows = []
    for index, lag in enumerate(lags):
        traded = monday + timedelta(days=7 * index)
        rows.append(
            make_row(
                index + 1,
                traded.isoformat(),
                Action.BUY,
                amount="-100",
                units="10",
                price="10",
                ticker="RY.TO",
                settle_date=(traded + timedelta(days=lag)).isoformat(),
            ),
        )
    return rows


def test_a_settle_date_far_outside_the_account_habit_is_reported() -> None:
    rows = trades_with_lags([1, 1, 1, 1, 1, 30])
    result = check(rows, "settlement-dates")
    assert result.status is CheckStatus.WARN
    assert "far longer than that account usually takes" in details(result)


def test_the_move_from_t_plus_2_to_t_plus_1_is_not_an_outlier() -> None:
    """Both lags appear in a folio spanning May 2024, and both are correct."""
    assert check(trades_with_lags([2, 2, 2, 1, 1, 1]), "settlement-dates").status is (
        CheckStatus.OK
    )


def test_a_weekend_does_not_make_a_normal_lag_look_odd() -> None:
    """Thursday T+2 is four calendar days, which business days see as two."""
    rows = trades_with_lags([2, 2, 2, 2, 2])
    rows.append(
        make_row(
            99,
            "2024-02-15",  # a Thursday
            Action.BUY,
            amount="-100",
            units="10",
            price="10",
            ticker="RY.TO",
            settle_date="2024-02-19",  # the Monday, still T+2
        ),
    )
    assert check(rows, "settlement-dates").status is CheckStatus.OK


def test_an_account_with_too_few_trades_is_not_judged() -> None:
    assert check(trades_with_lags([1, 30]), "settlement-dates").status is CheckStatus.OK


# --- transfers ----------------------------------------------------------------


def test_a_transfer_with_one_leg_is_reported() -> None:
    rows = [
        make_row(
            1,
            "2025-02-10",
            Action.TFR_OUT,
            units="-40",
            ticker="VFV.TO",
            account="WS-TFSA",
        ),
    ]
    result = check(rows, "transfers")
    assert result.status is CheckStatus.WARN
    assert "no other account records the matching side" in details(result)


def test_a_paired_transfer_passes() -> None:
    rows = [
        make_row(
            1,
            "2025-02-10",
            Action.BUY,
            amount="-400",
            units="40",
            price="10",
            ticker="VFV.TO",
            account="WS-TFSA",
        ),
        make_row(
            2,
            "2025-02-11",
            Action.TFR_OUT,
            units="-40",
            ticker="VFV.TO",
            account="WS-TFSA",
        ),
        make_row(
            3,
            "2025-02-11",
            Action.TFR_IN,
            units="40",
            ticker="VFV.TO",
            account="IBKR-TFSA",
        ),
    ]
    assert check(rows, "transfers").status is CheckStatus.OK


# --- fee conventions ----------------------------------------------------------


def test_a_trade_matching_neither_convention_is_reported() -> None:
    rows = [
        make_row(
            1,
            "2024-01-03",
            Action.BUY,
            amount="-5000",
            units="10",
            price="10",
            fee="5",
            ticker="RY.TO",
        ),
    ]
    result = check(rows, "fee-conventions")
    assert result.status is CheckStatus.WARN
    assert "does not close the gap" in details(result)


def test_a_fee_included_amount_reconciles() -> None:
    rows = [
        make_row(
            1,
            "2024-01-03",
            Action.BUY,
            amount="-1005",
            units="100",
            price="10",
            fee="5",
            ticker="RY.TO",
        ),
    ]
    assert check(rows, "fee-conventions").status is CheckStatus.OK


# --- configuration ------------------------------------------------------------


def test_a_disabled_check_does_not_run() -> None:
    results = run_checks(
        replay_of(oversold_in_one_account()),
        StubConfig(disabled=["unit-balances"]),
    )
    assert "unit-balances" not in {result.slug for result in results}


def test_an_ignored_ticker_is_excluded_from_every_check() -> None:
    result = check(
        oversold_in_one_account(),
        "unit-balances",
        StubConfig(tickers=["FTS.TO"]),
    )
    assert result.status is CheckStatus.OK


def test_an_ignored_account_is_excluded_from_every_check() -> None:
    rows = [
        make_row(
            1,
            "2024-06-03",
            Action.BUY,
            amount="-500",
            units="5",
            price="100",
            ticker="XEQT.TO",
            account="QT-RRSP",
        ),
    ]
    assert (
        check(
            rows,
            "cash-balances",
            StubConfig(accounts=["QT-RRSP"]),
        ).status
        is CheckStatus.OK
    )


def test_a_retired_account_ending_at_zero_is_not_reported() -> None:
    rows = [
        make_row(
            1,
            "2023-01-05",
            Action.CONTRIBUTION,
            amount="1000",
            ticker=None,
            account="QT-TFSA",
        ),
        make_row(
            2,
            "2023-05-05",
            Action.WITHDRAWAL,
            amount="-1000",
            ticker=None,
            account="QT-TFSA",
        ),
    ]
    assert check(rows, "cash-balances").status is CheckStatus.OK


def test_an_unknown_disabled_slug_is_rejected() -> None:
    with pytest.raises(UnknownCheckError, match="unit-balence"):
        run_checks(replay_of(clean_folio()), StubConfig(disabled=["unit-balence"]))


def test_validate_slugs_accepts_every_real_check() -> None:
    validate_slugs(CHECK_SLUGS)


# --- the command --------------------------------------------------------------


def seed_clean_folio() -> None:
    """Seed a folio the command should pass."""
    seed_fx({"2025-08-14": "1.3500", "2025-08-15": "1.3600"})
    seed_transaction(
        action="CONTRIBUTION",
        date="2025-08-14",
        settle_date="2025-08-14",
        account="IBKR-PERSONAL",
        currency="CAD",
        ticker=None,
        amount="5000",
        price=None,
        units=None,
    )
    seed_transaction(
        action="BUY",
        date="2025-08-14",
        settle_date="2025-08-14",
        account="IBKR-PERSONAL",
        currency="CAD",
        ticker="RY.TO",
        amount="-1000",
        price="10",
        units="100",
    )


def seed_oversold_folio() -> None:
    """Seed a folio with a sale of units that were never bought."""
    seed_fx({"2025-08-14": "1.3500", "2025-08-15": "1.3600"})
    seed_transaction(
        action="SELL",
        date="2025-08-15",
        settle_date="2025-08-15",
        account="IBKR-PERSONAL",
        currency="CAD",
        ticker="RY.TO",
        amount="600",
        price="12",
        units="-50",
    )


def test_check_reports_a_clean_folio(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_clean_folio()
        result = run_cli_with_config(ctx.config, app, ["check"])
    assert result.exit_code == 0
    assert_in_output("Unit balances", result)
    assert_in_output("checks pass", result)


def test_check_exits_non_zero_when_a_check_fails(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_oversold_folio()
        result = run_cli_with_config(ctx.config, app, ["check"])
    assert result.exit_code == 1
    assert_in_output("check failed", result)


def test_a_passing_check_prints_no_findings(temp_ctx: TempContext) -> None:
    """A correct result must never appear under a failing heading."""
    with temp_ctx() as ctx:
        seed_oversold_folio()
        result = run_cli_with_config(ctx.config, app, ["check"])
    # Settlement dates passes here, so nothing may be listed beneath it.
    assert_in_output("Settlement dates", result)
    assert_not_in_output("settles 2025", result)


def test_only_narrows_to_one_check(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_oversold_folio()
        result = run_cli_with_config(
            ctx.config,
            app,
            ["check", "--only", "unit-balances"],
        )
    assert_in_output("Unit balances", result)
    assert_not_in_output("Settlement dates", result)


def test_only_rejects_a_name_that_is_not_a_check(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_clean_folio()
        result = run_cli_with_config(
            ctx.config,
            app,
            ["check", "--only", "unit-balence"],
        )
    assert result.exit_code == 1
    assert_in_output("There is no check called 'unit-balence'", result)
    assert_in_output("unit-balances", result)


def test_json_output_is_machine_readable(temp_ctx: TempContext) -> None:
    with temp_ctx() as ctx:
        seed_oversold_folio()
        result = run_cli_with_config(ctx.config, app, ["check", "--json"])
    payload = json.loads(result.plain_output)
    assert payload["failed"] >= 1
    slugs = {entry["slug"] for entry in payload["checks"]}
    assert slugs == set(CHECK_SLUGS)


def test_a_folio_with_only_warnings_still_exits_zero(temp_ctx: TempContext) -> None:
    """A warning is worth seeing, but it must not fail a script that gates on this."""
    with temp_ctx() as ctx:
        seed_fx({"2025-08-14": "1.3500"})
        seed_transaction(
            action="CONTRIBUTION",
            date="2025-08-14",
            settle_date="2025-08-14",
            account="IBKR-PERSONAL",
            currency="CAD",
            ticker=None,
            amount="5000",
            price=None,
            units=None,
        )
        seed_transaction(
            action="TFR_OUT",
            date="2025-08-14",
            settle_date="2025-08-14",
            account="IBKR-PERSONAL",
            currency="CAD",
            ticker=None,
            amount="-100",
            price=None,
            units=None,
        )
        result = run_cli_with_config(ctx.config, app, ["check"])
    assert result.exit_code == 0
    assert_in_output("1 warning.", result)


def test_a_long_subject_moves_the_detail_onto_its_own_line() -> None:
    """A subject too wide to sit beside its detail must not push it off the edge."""
    # Wider than the test console leaves room for beside a wrapped sentence.
    subject = f"{'A-VERY-LONG-ACCOUNT-NAME' * 3} CAD"
    result = CheckResult(
        name="Cash balances",
        slug="cash-balances",
        status=CheckStatus.FAIL,
        summary="1 account goes negative",
        findings=(
            CheckFinding(
                subject=subject,
                detail="first goes negative on 2024-06-03.",
            ),
        ),
    )
    with capture_output() as captured:
        _print_findings(result)
    lines = [line for line in captured.get_text().splitlines() if line.strip()]
    # The subject owns its line, and no line runs past the console width.
    assert lines[0].strip().startswith("A-VERY-LONG-ACCOUNT-NAME")
    assert "first goes negative" in lines[1]
    assert all(len(line) <= 120 for line in lines)


def test_a_split_both_missing_and_duplicated_reads_as_recorded_wrong() -> None:
    rows = [
        *split_missing_from_one_account(),
        make_row(
            4,
            "2024-01-08",
            Action.BUY,
            amount="-2000",
            units="25",
            price="80",
            ticker="SCHD",
            account="WS-TFSA",
            currency="USD",
        ),
        make_row(
            5,
            "2024-10-10",
            Action.SPLIT,
            units="3",
            price="1",
            ticker="SCHD",
            account="IBKR-RRSP",
            currency="USD",
        ),
    ]
    result = check(rows, "split-coverage")
    assert result.summary == "2 splits are recorded wrong"


def test_a_cached_run_reports_exactly_what_a_fresh_one_does(
    temp_ctx: TempContext,
) -> None:
    """The cache carries diagnostics now, so the second run must not differ."""
    with temp_ctx() as ctx:
        seed_oversold_folio()
        first = run_cli_with_config(ctx.config, app, ["check", "--json"])
        # The cache is written by the first run; the second reads it back.
        assert ctx.config.acb_parquet.with_suffix(".meta.json").exists()
        second = run_cli_with_config(ctx.config, app, ["check", "--json"])
    assert json.loads(first.plain_output) == json.loads(second.plain_output)


def test_the_cache_carries_the_diagnostics(temp_ctx: TempContext) -> None:
    with temp_ctx():
        seed_oversold_folio()
        built = load_or_build()
        assert not built.from_cache
        cached = load_or_build()
        assert cached.from_cache
        assert cached.result is not None
        assert [w.code for w in cached.result.warnings] == [
            w.code for w in (built.result.warnings if built.result else [])
        ]


def test_editing_the_folio_invalidates_the_cached_diagnostics(
    temp_ctx: TempContext,
) -> None:
    """A fingerprint change must drop the warnings along with the figures."""
    with temp_ctx():
        seed_clean_folio()
        assert load_or_build().result is not None
        assert load_or_build().from_cache
        # A new transaction moves the fingerprint.
        seed_transaction(
            action="SELL",
            date="2025-08-15",
            settle_date="2025-08-15",
            account="IBKR-PERSONAL",
            currency="CAD",
            ticker="RY.TO",
            amount="9999",
            price="12",
            units="-9999",
        )
        rebuilt = load_or_build()
        assert not rebuilt.from_cache
        assert rebuilt.result is not None
        assert WarningCode.OVERSELL in {w.code for w in rebuilt.result.warnings}


def test_check_still_works_when_the_cache_holds_no_snapshot(
    temp_ctx: TempContext,
) -> None:
    """An older cache has no diagnostics in it; replaying is the fallback."""
    with temp_ctx() as ctx:
        seed_oversold_folio()
        run_cli_with_config(ctx.config, app, ["check"])
        meta_path = ctx.config.acb_parquet.with_suffix(".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        del meta["replay"]
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        result = run_cli_with_config(ctx.config, app, ["check"])
    assert result.exit_code == 1
    assert_in_output("Unit balances", result)
