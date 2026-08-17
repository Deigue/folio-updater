"""Tests for the cost-base replay engine."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from engine.events import ReplayResult, TxnRow, to_txn_rows
from engine.frames import acb_summary_frame, master_frame, scope_column
from engine.fx_rates import FxRates
from engine.replay import (
    ReplayConfig,
    detect_fee_conventions,
    detect_fee_signs,
    replay,
    resolve_trade_cash,
)
from engine.transfers import pair_transfers
from services.symbols import SymbolResolver
from utils.constants import (
    AccountType,
    Action,
    Column,
    Currency,
    FeeConvention,
    Scope,
    WarningCode,
)

if TYPE_CHECKING:
    from .test_types import TempContext

D = Decimal

FLAT_FX = FxRates(("2020-01-01",), (D("1.30"),))


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


def run(
    rows: list[TxnRow],
    fx: FxRates = FLAT_FX,
    types: dict[str, AccountType] | None = None,
    conventions: dict[str, FeeConvention] | None = None,
    aliases: list[tuple[str, str, str]] | None = None,
) -> ReplayResult:
    """Replay rows with explicit account facts, so no config is involved.

    Fee signs are detected exactly as `ReplayConfig.build` detects them, since
    which sign means "charged" is read from the rows rather than configured.
    """
    accounts = {row.account for row in rows}
    cfg = ReplayConfig(
        account_types=types or dict.fromkeys(accounts, AccountType.NON_REGISTERED),
        fee_conventions=conventions or dict.fromkeys(accounts, FeeConvention.AUTO),
        symbols=SymbolResolver(aliases or []),
        fee_signs=detect_fee_signs(rows),
    )
    return replay(rows, fx, cfg)


def codes(result: ReplayResult) -> set[WarningCode]:
    """Every distinct diagnostic a replay raised."""
    return {warning.code for warning in result.warnings}


# --- the adjustedcostbase.ca worked example -----------------------------------


def test_acb_matches_adjustedcostbase() -> None:
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                fee="5",
            ),
            make_row(
                2,
                "2024-02-02",
                Action.BUY,
                amount="-600",
                units="50",
                price="12",
                fee="5",
            ),
            make_row(
                3,
                "2024-03-04",
                Action.SELL,
                amount="900",
                units="-60",
                price="15",
                fee="5",
            ),
        ],
    )
    first, second, sale = (row.acct for row in result.rows)

    assert first.acb_cad == D("1005")
    assert second.acb_cad == D("1610")
    assert second.units == D("150")

    # Removed 1610 * 60/150 = 644 against proceeds of 900 - 5.
    assert sale.units == D("90")
    assert sale.acb_cad == D("966")
    assert sale.gain_cad == D("251")
    assert result.rows[2].proceeds_cad == D("895")


def test_sell_leaves_average_cost_invariant() -> None:
    """Numerator and denominator both scale by (1 - fraction)."""
    for sold in ("1", "25", "60", "99"):
        result = run(
            [
                make_row(
                    1,
                    "2024-01-02",
                    Action.BUY,
                    amount="-1000",
                    units="100",
                    price="10",
                    fee="5",
                ),
                make_row(
                    2,
                    "2024-03-04",
                    Action.SELL,
                    amount=f"{int(sold) * 15}",
                    units=f"-{sold}",
                    price="15",
                ),
            ],
        )
        before, after = result.rows[0].acct, result.rows[1].acct
        assert after.avg_cad == before.avg_cad, sold


def test_oversell_goes_negative_and_warns() -> None:
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10",
            ),
            make_row(
                2, "2024-03-04", Action.SELL, amount="2250", units="-150", price="15",
            ),
        ],
    )
    final = result.rows[-1].acct
    assert final.units == D("-50")
    # All remaining cost base comes off; the deficit stays visible.
    assert final.acb_cad == D("0")
    assert final.gain_cad == D("1250")
    assert WarningCode.OVERSELL in codes(result)
    assert WarningCode.NEGATIVE_FINAL_POSITION in codes(result)


# --- dual currency -------------------------------------------------------------


def test_usd_uses_settle_date_rate() -> None:
    """FolioACB is accumulated per row, not converted at one closing rate."""
    fx = FxRates(("2024-01-02", "2024-02-02"), (D("1.30"), D("1.40")))
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="10",
                price="100",
                currency="USD",
            ),
            make_row(
                2,
                "2024-02-02",
                Action.BUY,
                amount="-1000",
                units="10",
                price="100",
                currency="USD",
            ),
        ],
        fx,
    )
    folio = result.rows[-1].folio
    assert folio.acb_usd == D("2000")
    assert folio.acb_cad == D("2700")
    assert folio.avg_usd == D("100")
    assert folio.avg_cad == D("135")
    # The headline regression: today's rate would give 2800.
    assert folio.acb_cad != folio.acb_usd * D("1.40")


def test_sell_gain_carries_the_fx_component() -> None:
    """CAD cost base comes off proportionally; proceeds are converted."""
    fx = FxRates(("2024-01-02", "2024-06-03"), (D("1.30"), D("1.40")))
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="10",
                price="100",
                currency="USD",
            ),
            make_row(
                2,
                "2024-06-03",
                Action.SELL,
                amount="1000",
                units="-10",
                price="100",
                currency="USD",
            ),
        ],
        fx,
    )
    sale = result.rows[-1].acct
    # Flat in USD, but the loonie moved: 1000*1.40 - 1300 = 100 of CAD gain.
    assert sale.gain_usd == D("0")
    assert sale.gain_cad == D("100")


def test_cad_holding_has_blank_usd_columns() -> None:
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10",
            ),
        ],
    )
    frame = master_frame(result)
    for scope in Scope:
        assert frame[scope_column(scope, "ACB")].iloc[0] == pytest.approx(1000)
        assert pd.isna(frame[scope_column(scope, "ACB_USD")].iloc[0])
        assert pd.isna(frame[scope_column(scope, "Avg_USD")].iloc[0])
        assert pd.isna(frame[scope_column(scope, "Gain_USD")].iloc[0])
    assert pd.isna(frame["FXRate"].iloc[0])


# --- return of capital, splits -------------------------------------------------


def test_roc_reduces_acb_and_moves_no_cash() -> None:
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10",
            ),
            make_row(2, "2024-05-02", Action.ROC, amount="250"),
        ],
    )
    after = result.rows[-1].acct
    assert after.acb_cad == D("750")
    assert after.units == D("100")
    # Cash still reflects only the buy: the distribution was paid as a dividend
    # long before, so booking the reclassification as cash would double count.
    cash = result.cash[(Scope.ACCOUNT, "IBKR-PERSONAL", Currency.CAD)]
    assert cash.cash == D("-1000")
    assert cash.dividends == D("0")


def test_roc_floors_at_zero() -> None:
    result = run(
        [
            make_row(1, "2024-01-02", Action.BUY, amount="-60", units="10", price="6"),
            make_row(2, "2024-05-02", Action.ROC, amount="100"),
        ],
    )
    after = result.rows[-1].acct
    assert after.acb_cad == D("0")
    assert after.gain_cad == D("40")
    assert WarningCode.ROC_EXCEEDS_ACB in codes(result)


def test_split_preserves_acb() -> None:
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10",
            ),
            make_row(2, "2024-06-10", Action.SPLIT, units="10", price="1"),
        ],
    )
    before, after = result.rows[0].acct, result.rows[1].acct
    assert after.units == D("1000")
    assert after.acb_cad == before.acb_cad
    assert after.avg_cad == before.avg_cad / 10
    assert after.delta_cad == D("0")


def test_duplicate_split_applied_once() -> None:
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10",
            ),
            make_row(2, "2024-06-10", Action.SPLIT, units="10", price="1"),
            make_row(3, "2024-06-10", Action.SPLIT, units="10", price="1"),
        ],
    )
    assert result.rows[-1].acct.units == D("1000")
    assert WarningCode.DUPLICATE_SPLIT in codes(result)


def test_split_in_two_accounts_pools_once_but_splits_each() -> None:
    """One corporate action, reported by both brokers holding the security."""
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-100",
                units="10",
                price="10",
                account="IBKR-TFSA",
            ),
            make_row(
                2,
                "2024-01-02",
                Action.BUY,
                amount="-200",
                units="20",
                price="10",
                account="WS-TFSA",
            ),
            make_row(
                3,
                "2024-06-10",
                Action.SPLIT,
                units="10",
                price="1",
                account="IBKR-TFSA",
            ),
            make_row(
                4, "2024-06-10", Action.SPLIT, units="10", price="1", account="WS-TFSA",
            ),
        ],
        types={"IBKR-TFSA": AccountType.TFSA, "WS-TFSA": AccountType.TFSA},
    )
    # Each account really did split; the pooled type must not split twice.
    assert result.rows[2].acct.units == D("100")
    assert result.rows[3].acct.units == D("200")
    assert result.rows[3].type.units == D("300")
    assert WarningCode.DUPLICATE_SPLIT not in codes(result)


def test_split_without_position_warns() -> None:
    result = run([make_row(1, "2024-06-10", Action.SPLIT, units="10", price="1")])
    assert result.rows[0].acct.units == D("0")
    assert WarningCode.SPLIT_WITHOUT_POSITION in codes(result)


# --- income --------------------------------------------------------------------


def test_dividend_never_touches_acb_but_is_converted() -> None:
    fx = FxRates(("2024-01-02", "2024-04-01"), (D("1.30"), D("1.35")))
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="10",
                price="100",
                currency="USD",
            ),
            make_row(2, "2024-04-01", Action.DIVIDEND, amount="20", currency="USD"),
        ],
        fx,
    )
    dividend_row = result.rows[-1]
    assert dividend_row.acct.acb_cad == result.rows[0].acct.acb_cad
    assert dividend_row.acct.delta_cad == D("0")
    assert dividend_row.dividend_usd == D("20")
    assert dividend_row.dividend_cad == D("27.00")
    assert result.cash[(Scope.ACCOUNT, "IBKR-PERSONAL", Currency.USD)].dividends == D(
        "20",
    )


def test_negative_dividend_reversal_is_not_coerced_positive() -> None:
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.BUY, amount="-1000", units="10", price="100",
            ),
            make_row(2, "2024-04-01", Action.DIVIDEND, amount="-15"),
            make_row(3, "2024-04-01", Action.DIVIDEND, amount="18"),
        ],
    )
    cash = result.cash[(Scope.ACCOUNT, "IBKR-PERSONAL", Currency.CAD)]
    assert cash.dividends == D("3")


def test_income_without_position_warns() -> None:
    result = run([make_row(1, "2024-04-01", Action.DIVIDEND, amount="20")])
    assert WarningCode.INCOME_WITHOUT_POSITION in codes(result)


def test_dividend_shortly_after_a_sale_is_not_orphaned() -> None:
    """A distribution is earned on its ex-date and paid weeks later.

    Selling in between is the ordinary case, not a data error.
    """
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10",
            ),
            make_row(
                2, "2024-03-01", Action.SELL, amount="1200", units="-100", price="12",
            ),
            make_row(3, "2024-03-20", Action.DIVIDEND, amount="20"),
        ],
    )
    assert WarningCode.INCOME_WITHOUT_POSITION not in codes(result)


def test_dividend_long_after_a_sale_is_still_orphaned() -> None:
    """Past the tail the position is genuinely gone, so income is a finding."""
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10",
            ),
            make_row(
                2, "2024-03-01", Action.SELL, amount="1200", units="-100", price="12",
            ),
            make_row(3, "2024-08-20", Action.DIVIDEND, amount="20"),
        ],
    )
    assert WarningCode.INCOME_WITHOUT_POSITION in codes(result)


def test_the_closing_row_still_counts_as_a_day_held() -> None:
    """The tail is measured from the sale, not from the last row before it.

    A position transferred out in one row held units right up to that moment;
    measuring from the preceding buy would restart the clock far too early.
    """
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10",
            ),
            make_row(2, "2024-06-01", Action.TFR_OUT, units="100", ticker="TEST"),
            make_row(3, "2024-06-20", Action.DIVIDEND, amount="20"),
        ],
    )
    assert WarningCode.INCOME_WITHOUT_POSITION not in codes(result)


# --- scopes ---------------------------------------------------------------------


def test_all_scopes_in_one_pass() -> None:
    """A buy in one TFSA and a sell in another: the pool nets, the account does not."""
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                account="QT-TFSA",
            ),
            make_row(
                2,
                "2024-03-04",
                Action.SELL,
                amount="1200",
                units="-100",
                price="12",
                account="WS-TFSA",
            ),
        ],
        types={"QT-TFSA": AccountType.TFSA, "WS-TFSA": AccountType.TFSA},
    )
    sale = result.rows[-1]
    assert sale.acct.units == D("-100")
    assert sale.type.units == D("0")
    assert sale.folio.units == D("0")
    assert sale.type.gain_cad == D("200")
    oversells = [w for w in result.warnings if w.code is WarningCode.OVERSELL]
    assert [w.scope for w in oversells] == [Scope.ACCOUNT]


def test_taxable_pool_excludes_registered() -> None:
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                account="IBKR-PERSONAL",
            ),
            make_row(
                2,
                "2024-01-03",
                Action.BUY,
                amount="-2000",
                units="100",
                price="20",
                account="IBKR-TFSA",
            ),
        ],
        types={
            "IBKR-PERSONAL": AccountType.NON_REGISTERED,
            "IBKR-TFSA": AccountType.TFSA,
        },
    )
    non_registered = result.rows[0]
    assert non_registered.type.acb_cad == D("1000")
    assert result.rows[1].type.acb_cad == D("2000")
    assert result.rows[1].folio.acb_cad == D("3000")


# --- transfers -------------------------------------------------------------------


def test_transfer_pair_moves_acb_cross_account() -> None:
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                account="QT-TFSA",
            ),
            make_row(
                2,
                "2024-05-01",
                Action.TFR_OUT,
                amount="0",
                units="-100",
                account="QT-TFSA",
            ),
            make_row(
                3,
                "2024-05-01",
                Action.TFR_IN,
                amount="0",
                units="100",
                account="WS-TFSA",
            ),
        ],
        types={"QT-TFSA": AccountType.TFSA, "WS-TFSA": AccountType.TFSA},
    )
    out_leg, in_leg = result.rows[1], result.rows[2]
    assert out_leg.acct.units == D("0")
    assert out_leg.acct.acb_cad == D("0")
    assert in_leg.acct.units == D("100")
    assert in_leg.acct.acb_cad == D("1000")
    # Neither leg is a disposition, and the pooled type never noticed.
    assert in_leg.acct.gain_cad == D("0")
    assert in_leg.type.units == D("100")
    assert in_leg.type.acb_cad == D("1000")


def test_transfer_pair_moves_acb_across_a_currency_journal() -> None:
    """Norbert's Gambit: same account, two symbols, two currencies."""
    fx = FxRates(("2024-01-02", "2024-05-01"), (D("1.30"), D("1.25")))
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1300",
                units="100",
                price="13",
                ticker="DLR.TO",
                account="IBKR-PERSONAL",
            ),
            make_row(
                2,
                "2024-05-01",
                Action.TFR_OUT,
                amount="0",
                units="-100",
                ticker="DLR.TO",
                account="IBKR-PERSONAL",
            ),
            make_row(
                3,
                "2024-05-01",
                Action.TFR_IN,
                amount="0",
                units="100",
                ticker="DLR.U.TO",
                currency="USD",
                account="IBKR-PERSONAL",
            ),
        ],
        fx,
    )
    in_leg = result.rows[2]
    # CAD is the invariant; the USD figure derives from it at that day's rate.
    assert in_leg.acct.acb_cad == D("1300")
    assert in_leg.acct.acb_usd == D("1040")
    assert in_leg.acct.gain_cad == D("0")


def test_transfer_never_reads_amount_on_a_leg() -> None:
    """The journal legs carry a literal zero; a wrong Amount must not price them."""
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                account="QT-TFSA",
            ),
            make_row(
                2,
                "2024-05-01",
                Action.TFR_OUT,
                amount="-99999",
                units="-100",
                account="QT-TFSA",
            ),
            make_row(
                3,
                "2024-05-01",
                Action.TFR_IN,
                amount="99999",
                units="100",
                account="WS-TFSA",
            ),
        ],
        types={"QT-TFSA": AccountType.TFSA, "WS-TFSA": AccountType.TFSA},
    )
    assert result.rows[2].acct.acb_cad == D("1000")


def test_partial_transfer_carries_a_proportional_slice() -> None:
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                account="QT-TFSA",
            ),
            make_row(2, "2024-05-01", Action.TFR_OUT, units="-40", account="QT-TFSA"),
            make_row(3, "2024-05-01", Action.TFR_IN, units="40", account="WS-TFSA"),
        ],
        types={"QT-TFSA": AccountType.TFSA, "WS-TFSA": AccountType.TFSA},
    )
    assert result.rows[1].acct.acb_cad == D("600")
    assert result.rows[2].acct.acb_cad == D("400")


def test_transfer_unpaired_warns() -> None:
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10",
            ),
            make_row(2, "2024-05-01", Action.TFR_OUT, units="-100"),
        ],
    )
    assert WarningCode.TRANSFER_UNPAIRED in codes(result)
    # Units leave, but with no counterpart there is nowhere for the cost to go.
    assert result.rows[-1].acct.units == D("0")
    assert result.rows[-1].acct.acb_cad == D("1000")


def test_cash_transfer_moves_no_acb() -> None:
    result = run(
        [
            make_row(
                1,
                "2024-05-01",
                Action.TFR_OUT,
                amount="-500",
                ticker=None,
                account="QT-TFSA",
            ),
            make_row(
                2,
                "2024-05-01",
                Action.TFR_IN,
                amount="500",
                ticker=None,
                account="WS-TFSA",
            ),
        ],
        types={"QT-TFSA": AccountType.TFSA, "WS-TFSA": AccountType.TFSA},
    )
    out_cash = result.cash[(Scope.ACCOUNT, "QT-TFSA", Currency.CAD)]
    in_cash = result.cash[(Scope.ACCOUNT, "WS-TFSA", Currency.CAD)]
    assert out_cash.cash == D("-500")
    assert in_cash.cash == D("500")
    # A transfer consumes no contribution room.
    assert in_cash.contributions == D("0")
    assert result.cash[(Scope.TYPE, "TFSA", Currency.CAD)].cash == D("0")


def test_pair_transfers_prefers_the_cross_account_same_symbol_match() -> None:
    rows = [
        make_row(1, "2024-05-01", Action.TFR_OUT, units="-100", account="QT-TFSA"),
        make_row(
            2,
            "2024-05-01",
            Action.TFR_IN,
            units="100",
            ticker="OTHER",
            account="QT-TFSA",
        ),
        make_row(3, "2024-05-01", Action.TFR_IN, units="100", account="WS-TFSA"),
    ]
    pairs, unpaired = pair_transfers(rows)
    assert len(pairs) == 1
    assert pairs[0].in_leg.txn_id == 3
    assert [row.txn_id for row in unpaired] == [2]


# --- cash ------------------------------------------------------------------------


def test_cash_orders_by_settle_date() -> None:
    """A buy funded by a same-day contribution must not dip negative."""
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                settle_date="2024-01-04",
            ),
            make_row(
                2,
                "2024-01-04",
                Action.CONTRIBUTION,
                amount="1000",
                ticker=None,
                settle_date="2024-01-04",
            ),
        ],
    )
    assert result.cash[(Scope.ACCOUNT, "IBKR-PERSONAL", Currency.CAD)].cash == D("0")
    assert WarningCode.CASH_NEGATIVE not in codes(result)


def test_sub_cent_cash_residue_is_not_an_overdraft() -> None:
    """A balance short by a fraction of a cent is rounding, not an overdraft.

    A broker sweeping an account to zero converts what *its* books say is left,
    and a balance reconstructed from stored amounts inherits their rounding. The
    accumulator stays exact; only the judgement is made at cent precision.
    """
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.CONTRIBUTION, amount="1000.00", ticker=None,
            ),
            make_row(
                2,
                "2024-01-03",
                Action.FXT,
                amount="-1000.000063",
                units="740.74",
                price="1.35",
                ticker=None,
            ),
        ],
    )
    cash = result.cash[(Scope.ACCOUNT, "IBKR-PERSONAL", Currency.CAD)].cash
    assert cash == D("-0.000063")  # the accumulator is still exact
    assert WarningCode.CASH_NEGATIVE not in codes(result)


def test_a_one_cent_overdraft_is_still_reported() -> None:
    """The smallest overdraft that can exist must survive the rounding.

    This is what separates judging in cents from a one-cent tolerance, which
    would swallow it.
    """
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.CONTRIBUTION, amount="1000.00", ticker=None,
            ),
            make_row(
                2,
                "2024-01-03",
                Action.FXT,
                amount="-1000.01",
                units="740.75",
                price="1.35",
                ticker=None,
            ),
        ],
    )
    cash = result.cash[(Scope.ACCOUNT, "IBKR-PERSONAL", Currency.CAD)].cash
    assert cash == D("-0.01")
    assert WarningCode.CASH_NEGATIVE in codes(result)


def test_cash_negative_reports_the_first_crossing_only() -> None:
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10",
            ),
            make_row(
                2, "2024-02-02", Action.BUY, amount="-1000", units="100", price="10",
            ),
        ],
    )
    crossings = [w for w in result.warnings if w.code is WarningCode.CASH_NEGATIVE]
    assert len(crossings) == 1
    assert crossings[0].txn_id == 1


def test_contributions_and_withdrawals_are_tracked_apart_from_cash() -> None:
    result = run(
        [
            make_row(1, "2024-01-02", Action.CONTRIBUTION, amount="7000", ticker=None),
            make_row(2, "2024-06-02", Action.WITHDRAWAL, amount="-2000", ticker=None),
        ],
    )
    cash = result.cash[(Scope.ACCOUNT, "IBKR-PERSONAL", Currency.CAD)]
    assert cash.cash == D("5000")
    assert cash.contributions == D("7000")
    assert cash.withdrawals == D("2000")


# --- FXT ---------------------------------------------------------------------------


def test_single_leg_fxt_books_the_fee_against_the_usd_leg() -> None:
    """IBKR shape: Amount is the CAD delta, Units the USD one, fee charged in USD."""
    result = run(
        [
            make_row(
                1,
                "2024-04-01",
                Action.FXT,
                amount="-13624",
                units="10000",
                price="1.3624",
                fee="2",
                ticker=None,
                account="IBKR-RRSP",
            ),
        ],
        types={"IBKR-RRSP": AccountType.RRSP},
    )
    assert result.cash[(Scope.ACCOUNT, "IBKR-RRSP", Currency.CAD)].cash == D("-13624")
    assert result.cash[(Scope.ACCOUNT, "IBKR-RRSP", Currency.USD)].cash == D("9998")


def test_two_leg_fxt_applies_each_amount_to_its_own_currency() -> None:
    """QuestTrade shape: paired rows, no Price or Units."""
    result = run(
        [
            make_row(
                1,
                "2024-04-01",
                Action.FXT,
                amount="-1000",
                ticker=None,
                account="QT-RRSP",
            ),
            make_row(
                2,
                "2024-04-01",
                Action.FXT,
                amount="735",
                currency="USD",
                ticker=None,
                account="QT-RRSP",
            ),
        ],
        types={"QT-RRSP": AccountType.RRSP},
    )
    assert result.cash[(Scope.ACCOUNT, "QT-RRSP", Currency.CAD)].cash == D("-1000")
    assert result.cash[(Scope.ACCOUNT, "QT-RRSP", Currency.USD)].cash == D("735")
    assert WarningCode.FXT_AMOUNT_INCONSISTENT not in codes(result)


def test_fxt_amount_inconsistent() -> None:
    """Real row 1818: a large leg rounded to a clean number."""
    result = run(
        [
            make_row(
                1,
                "2024-04-01",
                Action.FXT,
                amount="-13000",
                units="9541.98",
                price="1.3624",
                fee="2",
                ticker=None,
                account="IBKR-RRSP",
            ),
        ],
        types={"IBKR-RRSP": AccountType.RRSP},
    )
    assert WarningCode.FXT_AMOUNT_INCONSISTENT in codes(result)
    # The warning never substitutes a recomputed figure for what moved.
    assert result.cash[(Scope.ACCOUNT, "IBKR-RRSP", Currency.CAD)].cash == D("-13000")


# --- fee conventions -----------------------------------------------------------------


def test_fee_convention_auto_detects_both_brokers() -> None:
    rows = [
        # IBKR reports Amount gross of the commission.
        make_row(
            1,
            "2024-01-02",
            Action.BUY,
            amount="-1000",
            units="100",
            price="10",
            fee="1",
            account="IBKR-PERSONAL",
        ),
        make_row(
            2,
            "2024-01-03",
            Action.BUY,
            amount="-2000",
            units="200",
            price="10",
            fee="1",
            account="IBKR-PERSONAL",
        ),
        # QuestTrade folds it in.
        make_row(
            3,
            "2024-01-02",
            Action.BUY,
            amount="-1004.95",
            units="100",
            price="10",
            fee="4.95",
            account="QT-TFSA",
        ),
        make_row(
            4,
            "2024-01-03",
            Action.BUY,
            amount="-2004.95",
            units="200",
            price="10",
            fee="4.95",
            account="QT-TFSA",
        ),
    ]
    detected = detect_fee_conventions(rows)
    assert detected["IBKR-PERSONAL"] is FeeConvention.EXCLUDED
    assert detected["QT-TFSA"] is FeeConvention.INCLUDED


def test_included_and_excluded_agree_when_the_fee_is_zero() -> None:
    row = make_row(1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10")
    included = resolve_trade_cash(row, FeeConvention.INCLUDED)
    excluded = resolve_trade_cash(row, FeeConvention.EXCLUDED)
    assert included.cost == excluded.cost == D("1000")


def test_ambiguous_fee_convention_warns() -> None:
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1234.56",
                units="100",
                price="10",
                fee="5",
            ),
        ],
    )
    assert WarningCode.AMBIGUOUS_FEE_CONVENTION in codes(result)


def test_questrade_price_rounding_does_not_read_as_ambiguous() -> None:
    """QT rounds Price to 2dp, so Price*Units is out by up to half a cent a share."""
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1002.45",
                units="510",
                price="1.96",
                fee="4.95",
                account="QT-TFSA",
            ),
        ],
        types={"QT-TFSA": AccountType.TFSA},
    )
    assert WarningCode.AMBIGUOUS_FEE_CONVENTION not in codes(result)


# --- diagnostics and frames ---------------------------------------------------


def test_settle_before_trade_warns() -> None:
    result = run(
        [
            make_row(
                1,
                "2024-03-04",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                settle_date="2024-03-01",
            ),
        ],
    )
    assert WarningCode.SETTLE_BEFORE_TRADE in codes(result)


@pytest.mark.parametrize("action", [Action.CONTRIBUTION, Action.DIVIDEND])
def test_settle_before_trade_is_trades_only(action: Action) -> None:
    """Only an exchange of cash for units really settles.

    A contribution is credited the day the money lands and a dividend on its pay
    date, so for both the recorded date is a bookkeeping choice that can
    legitimately fall after the date the cash moved.
    """
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10",
            ),
            make_row(2, "2024-03-04", action, amount="500", settle_date="2024-03-01"),
        ],
    )
    assert WarningCode.SETTLE_BEFORE_TRADE not in codes(result)


def test_superficial_loss_suspect() -> None:
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10",
            ),
            make_row(
                2, "2024-03-04", Action.SELL, amount="700", units="-100", price="7",
            ),
            make_row(
                3, "2024-03-20", Action.BUY, amount="-720", units="100", price="7.20",
            ),
        ],
    )
    assert WarningCode.SUPERFICIAL_LOSS_SUSPECT in codes(result)


def test_superficial_loss_suspect_buy_precedes_sale() -> None:
    """The CRA window also covers the 30 days before the sale, not just after."""
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10",
            ),
            make_row(2, "2024-02-20", Action.BUY, amount="-350", units="50", price="7"),
            make_row(
                3, "2024-03-04", Action.SELL, amount="700", units="-100", price="7",
            ),
        ],
    )
    assert WarningCode.SUPERFICIAL_LOSS_SUSPECT in codes(result)


def test_superficial_loss_not_flagged_in_a_registered_account() -> None:
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                account="IBKR-TFSA",
            ),
            make_row(
                2,
                "2024-03-04",
                Action.SELL,
                amount="700",
                units="-100",
                price="7",
                account="IBKR-TFSA",
            ),
            make_row(
                3,
                "2024-03-20",
                Action.BUY,
                amount="-720",
                units="100",
                price="7.20",
                account="IBKR-TFSA",
            ),
        ],
        types={"IBKR-TFSA": AccountType.TFSA},
    )
    assert WarningCode.SUPERFICIAL_LOSS_SUSPECT not in codes(result)


def test_flags_include_codes_raised_after_the_row_was_emitted() -> None:
    """CASH_NEGATIVE comes out of the cash walk, long after the ACB walk."""
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10",
            ),
        ],
    )
    assert WarningCode.CASH_NEGATIVE in result.rows[0].flags


def test_master_frame_is_one_row_per_txn() -> None:
    rows = [
        make_row(1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10"),
        make_row(2, "2024-01-03", Action.CONTRIBUTION, amount="500", ticker=None),
        make_row(3, "2024-01-04", Action.SELL, amount="600", units="-50", price="12"),
    ]
    frame = master_frame(run(rows))
    assert len(frame) == len(rows)
    assert frame.index.is_unique
    assert set(frame.index) == {1, 2, 3}
    assert set(frame.columns) >= {
        str(Column.Txn.TXN_ID),
        str(Column.Txn.ACTION),
        "Symbol",
        "AcctType",
        "Impact",
        "Flags",
    }
    # A cash-only row tracks no position, so its scope measures stay blank.
    assert pd.isna(frame.loc[2, scope_column(Scope.ACCOUNT, "ACB")])


def test_acb_summary_frame_has_one_row_per_symbol() -> None:
    frame = master_frame(
        run(
            [
                make_row(
                    1,
                    "2024-01-02",
                    Action.BUY,
                    amount="-1000",
                    units="100",
                    price="10",
                    ticker="AAA",
                ),
                make_row(
                    2,
                    "2024-01-03",
                    Action.BUY,
                    amount="-500",
                    units="50",
                    price="10",
                    ticker="AAA",
                ),
                make_row(
                    3,
                    "2024-01-04",
                    Action.BUY,
                    amount="-800",
                    units="40",
                    price="20",
                    ticker="BBB",
                ),
            ],
        ),
    )
    summary = acb_summary_frame(frame)
    assert list(summary["Symbol"]) == ["AAA", "BBB"]
    assert summary.loc[0, scope_column(Scope.ACCOUNT, "ACB")] == pytest.approx(1500)
    assert summary.loc[0, scope_column(Scope.ACCOUNT, "Avg")] == pytest.approx(10)


def test_acb_summary_frame_carries_the_usd_average() -> None:
    """`Avg_USD` is derived like `Avg`, and stays blank for a CAD holding."""
    frame = master_frame(
        run(
            [
                make_row(
                    1,
                    "2024-01-02",
                    Action.BUY,
                    amount="-1000",
                    units="10",
                    price="100",
                    ticker="USDCO",
                    currency="USD",
                ),
                make_row(
                    2,
                    "2024-01-03",
                    Action.BUY,
                    amount="-800",
                    units="40",
                    price="20",
                    ticker="CADCO",
                ),
            ],
        ),
    )
    summary = acb_summary_frame(frame).set_index("Symbol")
    avg = scope_column(Scope.ACCOUNT, "Avg")
    avg_usd = scope_column(Scope.ACCOUNT, "Avg_USD")
    # FLAT_FX is 1.30, so the USD average is the CAD one over the rate.
    assert summary.loc["USDCO", avg] == pytest.approx(130)
    assert summary.loc["USDCO", avg_usd] == pytest.approx(100)
    assert pd.isna(summary.loc["CADCO", avg_usd])


def test_decimal_precision_survives_replay() -> None:
    """Thirds of a share must not drift into float noise."""
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-100.000001",
                units="0.333333",
                price="300.000003",
            ),
        ],
    )
    assert result.rows[0].acct.acb_cad == D("100.000001")
    assert result.rows[0].acct.units == D("0.333333")


def test_alias_resolution_is_time_bounded() -> None:
    """A symbol reused after its rename date is a different security."""
    aliases = [("SPLG", "SPYM", "2025-10-31")]
    result = run(
        [
            make_row(
                1,
                "2025-01-02",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                ticker="SPLG",
            ),
            make_row(
                2,
                "2025-12-01",
                Action.BUY,
                amount="-500",
                units="50",
                price="10",
                ticker="SPLG",
            ),
        ],
        aliases=aliases,
    )
    assert result.rows[0].symbol == "SPYM"
    assert result.rows[1].symbol == "SPLG"
    # Two different securities, so the second buy starts its own pool.
    assert result.rows[1].acct.acb_cad == D("500")


# --- edge cases ---------------------------------------------------------------


def test_roc_on_a_usd_holding_floors_both_currencies() -> None:
    fx = FxRates(("2024-01-02", "2024-05-02"), (D("1.30"), D("1.40")))
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-100",
                units="10",
                price="10",
                currency="USD",
            ),
            make_row(2, "2024-05-02", Action.ROC, amount="150", currency="USD"),
        ],
        fx,
    )
    after = result.rows[-1].acct
    assert after.acb_usd == D("0")
    assert after.acb_cad == D("0")
    assert after.gain_usd == D("50")
    # 150 USD converts to 210 CAD against a 130 CAD cost base.
    assert after.gain_cad == D("80")


def test_roc_without_a_position_warns() -> None:
    result = run([make_row(1, "2024-05-02", Action.ROC, amount="50")])
    assert WarningCode.INCOME_WITHOUT_POSITION in codes(result)


def test_split_with_a_non_positive_ratio_is_skipped() -> None:
    """Sign rules keep these out of the folio; the engine still must not divide."""
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10",
            ),
            make_row(2, "2024-06-10", Action.SPLIT, units="10", price="0"),
        ],
    )
    assert result.rows[-1].acct.units == D("100")


def test_unpaired_transfer_in_adds_units_without_cost() -> None:
    result = run([make_row(1, "2024-05-01", Action.TFR_IN, units="100")])
    assert result.rows[0].acct.units == D("100")
    assert result.rows[0].acct.acb_cad == D("0")
    assert WarningCode.TRANSFER_UNPAIRED in codes(result)


def test_unpaired_cash_transfer_moves_no_units() -> None:
    result = run(
        [make_row(1, "2024-05-01", Action.TFR_OUT, amount="-500", ticker=None)],
    )
    assert WarningCode.TRANSFER_UNPAIRED in codes(result)
    assert result.cash[(Scope.ACCOUNT, "IBKR-PERSONAL", Currency.CAD)].cash == D("-500")


def test_dust_snaps_to_zero_and_rolls_residual_into_gain() -> None:
    """A float-noise residue must not leave a position open forever."""
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10",
            ),
            make_row(
                2,
                "2024-03-04",
                Action.SELL,
                amount="1200",
                units="-99.9999999999",
                price="12",
            ),
        ],
    )
    final = result.rows[-1].acct
    assert final.units == D("0")
    assert final.acb_cad == D("0")
    # Everything the sale did not remove is realized rather than stranded.
    assert final.gain_cad == D("200")


def test_fch_is_income_and_charges_cash() -> None:
    result = run(
        [
            make_row(1, "2024-01-02", Action.FCH, amount="-9.95", ticker=None),
            make_row(2, "2024-02-02", Action.FCH, amount="500", ticker=None),
        ],
    )
    cash = result.cash[(Scope.ACCOUNT, "IBKR-PERSONAL", Currency.CAD)]
    assert cash.cash == D("490.05")
    # A charge counts as a fee; RSU income arriving as FCH does not.
    assert cash.fees == D("9.95")


def test_configured_fee_convention_skips_detection(temp_ctx: TempContext) -> None:
    overrides = {
        "accounts": {
            "map": {"QT-TFSA": {"type": "TFSA", "amount_includes_fees": True}},
        },
    }
    rows = [
        make_row(
            1,
            "2024-01-02",
            Action.BUY,
            amount="-1000",
            units="100",
            price="10",
            fee="4.95",
            account="QT-TFSA",
        ),
    ]
    with temp_ctx(overrides):
        detected = detect_fee_conventions(rows)
    assert detected["QT-TFSA"] is FeeConvention.INCLUDED


def test_a_near_even_fee_split_warns_and_still_decides(
    temp_ctx: TempContext,
    caplog: pytest.LogCaptureFixture,
) -> None:
    rows = [
        make_row(
            1,
            "2024-01-02",
            Action.BUY,
            amount="-1000",
            units="100",
            price="10",
            fee="5",
        ),
        make_row(
            2,
            "2024-01-03",
            Action.BUY,
            amount="-1005",
            units="100",
            price="10",
            fee="5",
        ),
    ]
    with temp_ctx(), caplog.at_level("WARNING"):
        detected = detect_fee_conventions(rows)
    assert detected["IBKR-PERSONAL"] in (FeeConvention.EXCLUDED, FeeConvention.INCLUDED)
    assert "splits near evenly" in caplog.text


def test_unknown_account_type_is_reported_once() -> None:
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                account="MYSTERY",
            ),
            make_row(
                2,
                "2024-01-03",
                Action.BUY,
                amount="-500",
                units="50",
                price="10",
                account="MYSTERY",
            ),
        ],
        types={"MYSTERY": AccountType.UNKNOWN},
    )
    unknown = [w for w in result.warnings if w.code is WarningCode.UNKNOWN_ACCOUNT_TYPE]
    assert len(unknown) == 1
    assert unknown[0].pool == "MYSTERY"
    assert unknown[0].txn_id is None


def test_margin_accounts_are_allowed_to_go_negative() -> None:
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                account="IBKR-MARGIN",
            ),
        ],
        types={"IBKR-MARGIN": AccountType.MARGIN},
    )
    assert WarningCode.CASH_NEGATIVE not in codes(result)


def test_empty_replay_produces_an_empty_frame() -> None:
    frame = master_frame(run([]))
    assert frame.empty
    assert acb_summary_frame(frame).empty


def test_summary_of_a_frame_with_no_positions_is_empty() -> None:
    frame = master_frame(
        run(
            [make_row(1, "2024-01-02", Action.CONTRIBUTION, amount="500", ticker=None)],
        ),
    )
    assert not frame.empty
    assert acb_summary_frame(frame).empty


def _raw_row(**overrides: object) -> pd.DataFrame:
    """One raw Txns record, the way pd.read_sql_query hands it back."""
    record: dict[str, object] = {
        str(Column.Txn.TXN_ID): 1,
        str(Column.Txn.TXN_DATE): "2024-01-02",
        str(Column.Txn.ACTION): "BUY",
        str(Column.Txn.AMOUNT): -100.0,
        str(Column.Txn.CURRENCY): "CAD",
        str(Column.Txn.ACCOUNT): "IBKR-PERSONAL",
        str(Column.Txn.TICKER): "TEST",
    }
    record.update(overrides)
    return pd.DataFrame([record])


def test_rows_with_an_unknown_action_are_dropped() -> None:
    assert to_txn_rows(_raw_row(Action="NOT_AN_ACTION")) == []
    assert to_txn_rows(pd.DataFrame()) == []


def test_an_unrecognised_currency_reads_as_cad() -> None:
    assert to_txn_rows(_raw_row(**{"$": ""}))[0].currency is Currency.CAD


def test_a_missing_ticker_reads_as_none() -> None:
    assert to_txn_rows(_raw_row(Ticker=None))[0].ticker is None


def test_pair_transfers_rejects_mismatched_shapes() -> None:
    """A cash leg never pairs with a position leg, whatever the numbers say."""
    rows = [
        make_row(1, "2024-05-01", Action.TFR_OUT, units="-100", account="QT-TFSA"),
        make_row(
            2,
            "2024-05-01",
            Action.TFR_IN,
            amount="100",
            ticker=None,
            account="WS-TFSA",
        ),
    ]
    pairs, unpaired = pair_transfers(rows)
    assert pairs == []
    assert len(unpaired) == 2


def test_pair_transfers_rejects_a_unit_mismatch() -> None:
    rows = [
        make_row(1, "2024-05-01", Action.TFR_OUT, units="-100", account="QT-TFSA"),
        make_row(2, "2024-05-01", Action.TFR_IN, units="99", account="WS-TFSA"),
    ]
    pairs, unpaired = pair_transfers(rows)
    assert pairs == []
    assert len(unpaired) == 2


def test_pair_transfers_rejects_same_account_same_symbol() -> None:
    """Same account and same symbol is not a move; it is two contradictory rows."""
    rows = [
        make_row(1, "2024-05-01", Action.TFR_OUT, units="-100"),
        make_row(2, "2024-05-01", Action.TFR_IN, units="100"),
    ]
    pairs, _ = pair_transfers(rows)
    assert pairs == []


def test_pair_transfers_accepts_a_cross_account_symbol_change() -> None:
    """A broker renaming the security in flight is still one move."""
    rows = [
        make_row(
            1,
            "2024-05-01",
            Action.TFR_OUT,
            units="-100",
            ticker="OLD",
            account="QT-TFSA",
        ),
        make_row(
            2,
            "2024-05-01",
            Action.TFR_IN,
            units="100",
            ticker="NEW",
            account="WS-TFSA",
        ),
    ]
    pairs, unpaired = pair_transfers(rows)
    assert len(pairs) == 1
    assert unpaired == []


def test_pair_transfers_rejects_a_cash_amount_mismatch() -> None:
    rows = [
        make_row(
            1,
            "2024-05-01",
            Action.TFR_OUT,
            amount="-500",
            ticker=None,
            account="QT-TFSA",
        ),
        make_row(
            2,
            "2024-05-01",
            Action.TFR_IN,
            amount="400",
            ticker=None,
            account="WS-TFSA",
        ),
    ]
    pairs, unpaired = pair_transfers(rows)
    assert pairs == []
    assert len(unpaired) == 2


def test_a_same_account_cash_journal_pairs() -> None:
    rows = [
        make_row(1, "2024-05-01", Action.TFR_OUT, amount="-500", ticker=None),
        make_row(2, "2024-05-01", Action.TFR_IN, amount="500", ticker=None),
    ]
    pairs, unpaired = pair_transfers(rows)
    assert len(pairs) == 1
    assert not pairs[0].moves_units
    assert unpaired == []


def test_a_cash_transfer_pair_carries_no_cost_base() -> None:
    """Both legs go through the pair path, and neither moves a position."""
    result = run(
        [
            make_row(
                1,
                "2024-05-01",
                Action.TFR_OUT,
                amount="-500",
                ticker=None,
                account="QT-TFSA",
            ),
            make_row(
                2,
                "2024-05-01",
                Action.TFR_IN,
                amount="500",
                ticker=None,
                account="WS-TFSA",
            ),
        ],
        types={"QT-TFSA": AccountType.TFSA, "WS-TFSA": AccountType.TFSA},
    )
    assert WarningCode.TRANSFER_UNPAIRED not in codes(result)
    assert all(row.acct.units == D("0") for row in result.rows)


def test_a_no_position_finding_is_reported_once_per_row() -> None:
    """One finding, not one per pool grain."""
    result = run([make_row(1, "2024-06-10", Action.SPLIT, units="10", price="1")])
    without_position = [
        w for w in result.warnings if w.code is WarningCode.SPLIT_WITHOUT_POSITION
    ]
    assert len(without_position) == 1
    assert without_position[0].scope is Scope.ACCOUNT


def test_a_diagnostic_is_not_duplicated_when_an_account_is_named_after_its_type() -> (
    None
):
    """Account pool `TFSA` and type pool `TFSA` are the same key; warn once."""
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                account="TFSA",
            ),
            make_row(
                2,
                "2024-03-04",
                Action.SELL,
                amount="2250",
                units="-150",
                price="15",
                account="TFSA",
            ),
        ],
        types={"TFSA": AccountType.TFSA},
    )
    oversells = [w for w in result.warnings if w.code is WarningCode.OVERSELL]
    # Account, type and folio would be three, but two of them share a pool key.
    assert [w.pool for w in oversells] == ["TFSA", "FOLIO"]


def test_a_transfer_leg_carrying_a_ticker_but_no_units_moves_only_cash() -> None:
    """Some brokers label a cash journal with the security it relates to."""
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                account="QT-TFSA",
            ),
            make_row(
                2,
                "2024-05-01",
                Action.TFR_OUT,
                amount="-500",
                units="0",
                account="QT-TFSA",
            ),
            make_row(
                3,
                "2024-05-01",
                Action.TFR_IN,
                amount="500",
                units="0",
                account="WS-TFSA",
            ),
        ],
        types={"QT-TFSA": AccountType.TFSA, "WS-TFSA": AccountType.TFSA},
    )
    assert WarningCode.TRANSFER_UNPAIRED not in codes(result)
    # The position stayed where it was; only cash moved.
    assert result.rows[1].acct.units == D("100")
    assert result.rows[1].acct.acb_cad == D("1000")
    assert result.rows[2].acct.units == D("0")
    assert result.cash[(Scope.ACCOUNT, "WS-TFSA", Currency.CAD)].cash == D("500")


def test_an_unpaired_leg_with_a_ticker_but_no_units_moves_nothing() -> None:
    result = run(
        [make_row(1, "2024-05-01", Action.TFR_OUT, amount="-500", units="0")],
    )
    assert WarningCode.TRANSFER_UNPAIRED in codes(result)
    assert result.rows[0].acct.units == D("0")


def test_pair_transfers_rejects_a_position_in_leg_for_a_cash_out_leg() -> None:
    rows = [
        make_row(
            1,
            "2024-05-01",
            Action.TFR_OUT,
            amount="-500",
            ticker=None,
            account="QT-TFSA",
        ),
        make_row(2, "2024-05-01", Action.TFR_IN, units="100", account="WS-TFSA"),
    ]
    pairs, unpaired = pair_transfers(rows)
    assert pairs == []
    assert len(unpaired) == 2


def test_pair_transfers_does_not_reuse_a_consumed_in_leg() -> None:
    """Two out legs of the same size must not both claim the one in leg."""
    rows = [
        make_row(1, "2024-05-01", Action.TFR_OUT, units="-100", account="QT-TFSA"),
        make_row(2, "2024-05-01", Action.TFR_OUT, units="-100", account="QT-RRSP"),
        make_row(3, "2024-05-01", Action.TFR_IN, units="100", account="WS-TFSA"),
    ]
    pairs, unpaired = pair_transfers(rows)
    assert len(pairs) == 1
    assert pairs[0].out_leg.txn_id == 1
    assert [row.txn_id for row in unpaired] == [2]


def test_summary_leaves_an_average_blank_when_a_measure_is_missing() -> None:
    """A cached frame can carry a blank cell; the average must not invent one."""
    frame = master_frame(
        run(
            [
                make_row(
                    1,
                    "2024-01-02",
                    Action.BUY,
                    amount="-1000",
                    units="100",
                    price="10",
                ),
            ],
        ),
    )
    frame.loc[1, scope_column(Scope.ACCOUNT, "ACB")] = None
    summary = acb_summary_frame(frame)
    assert pd.isna(summary.loc[0, scope_column(Scope.ACCOUNT, "Avg")])


# --- intra-day ordering ---------------------------------------------------------
#
# A date is the finest resolution the folio records, so several rows routinely
# share one and their true sequence is unrecoverable. These pin the ordering
# that goes wrong least often.


def test_a_same_day_buy_supplies_a_transfer_out() -> None:
    """The acquisition has to land before the transfer that moves it."""
    result = run(
        [
            make_row(
                1,
                "2024-03-04",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                account="QT-TFSA",
                settle_date="2024-03-06",
            ),
            make_row(2, "2024-03-04", Action.TFR_OUT, units="-100", account="QT-TFSA"),
            make_row(3, "2024-03-04", Action.TFR_IN, units="100", account="WS-TFSA"),
        ],
        types={"QT-TFSA": AccountType.TFSA, "WS-TFSA": AccountType.TFSA},
    )
    source = next(c for c in result.rows if c.row.txn_id == 2)
    destination = next(c for c in result.rows if c.row.txn_id == 3)
    assert source.acct.units == D("0")
    assert source.acct.acb_cad == D("0")
    # The cost base arrives with the shares rather than stranding at source.
    assert destination.acct.units == D("100")
    assert destination.acct.acb_cad == D("1000")


def test_a_same_day_buy_then_sell_is_not_an_oversell() -> None:
    result = run(
        [
            make_row(
                2, "2024-03-04", Action.SELL, amount="1200", units="-100", price="12",
            ),
            make_row(
                1, "2024-03-04", Action.BUY, amount="-1000", units="100", price="10",
            ),
        ],
    )
    assert WarningCode.OVERSELL not in codes(result)
    assert result.rows[-1].acct.units == D("0")
    assert result.rows[-1].acct.gain_cad == D("200")


def test_a_same_day_transfer_in_supplies_a_sale() -> None:
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                account="QT-TFSA",
            ),
            make_row(2, "2024-05-01", Action.TFR_OUT, units="-100", account="QT-TFSA"),
            make_row(3, "2024-05-01", Action.TFR_IN, units="100", account="WS-TFSA"),
            make_row(
                4,
                "2024-05-01",
                Action.SELL,
                amount="1400",
                units="-100",
                price="14",
                account="WS-TFSA",
            ),
        ],
        types={"QT-TFSA": AccountType.TFSA, "WS-TFSA": AccountType.TFSA},
    )
    assert WarningCode.OVERSELL not in codes(result)
    sale = next(c for c in result.rows if c.row.txn_id == 4)
    assert sale.acct.units == D("0")
    # Cost base came across with the shares, so the gain is 1400 - 1000.
    assert sale.acct.gain_cad == D("400")


def test_a_same_day_split_applies_before_the_sale() -> None:
    result = run(
        [
            make_row(
                1, "2024-01-02", Action.BUY, amount="-1000", units="100", price="10",
            ),
            make_row(
                3, "2024-06-10", Action.SELL, amount="1200", units="-500", price="2.4",
            ),
            make_row(2, "2024-06-10", Action.SPLIT, units="10", price="1"),
        ],
    )
    assert WarningCode.OVERSELL not in codes(result)
    assert result.rows[-1].acct.units == D("500")


def test_cost_base_follows_trade_date_not_settle_date() -> None:
    """Settlement lags differ by action, so settle order loses the true sequence.

    A T+2 buy traded on the 4th settles on the 6th, while a return of capital
    traded on the 5th settles the same day. Replaying by settle date would apply
    the distribution to the smaller pre-purchase cost base, floor it at zero and
    invent a capital gain.
    """
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-60",
                units="10",
                price="6",
                settle_date="2024-01-04",
            ),
            make_row(
                2,
                "2024-03-04",
                Action.BUY,
                amount="-940",
                units="10",
                price="94",
                settle_date="2024-03-06",
            ),
            make_row(
                3, "2024-03-05", Action.ROC, amount="200", settle_date="2024-03-05",
            ),
        ],
    )
    final = result.rows[-1].acct
    assert final.units == D("20")
    assert final.acb_cad == D("800")
    assert final.gain_cad == D("0")
    assert WarningCode.ROC_EXCEEDS_ACB not in codes(result)


def test_cash_follows_settle_date_not_trade_date() -> None:
    """The mirror of the rule above, and why the two walks cannot be merged.

    A buy traded on the 4th settles on the 6th; the contribution funding it
    settles on the 5th. Ordering cash by trade date would show the account
    overdrawn on a day it never was.
    """
    result = run(
        [
            make_row(
                1,
                "2024-03-04",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                settle_date="2024-03-06",
            ),
            make_row(
                2,
                "2024-03-05",
                Action.CONTRIBUTION,
                amount="1000",
                ticker=None,
                settle_date="2024-03-05",
            ),
        ],
    )
    assert WarningCode.CASH_NEGATIVE not in codes(result)
    assert result.cash[(Scope.ACCOUNT, "IBKR-PERSONAL", Currency.CAD)].cash == D("0")


# --- fee signs ------------------------------------------------------------------
#
# Which sign means "charged" is a broker convention, not a universal one. IBKR
# and QuestTrade write a charge negative; a hand-entered row and
# adjustedcostbase.ca write it positive. Reading only the magnitude would be
# safe if the two never mixed, but IBKR splits an order across fills and rebates
# part of the commission, so a positive fee turns up on an otherwise negative
# account and means the opposite thing.


def test_fee_sign_is_detected_per_account() -> None:
    rows = [
        make_row(
            1,
            "2024-01-02",
            Action.BUY,
            amount="-1000",
            units="100",
            price="10",
            fee="-0.35",
            account="IBKR-PERSONAL",
        ),
        make_row(
            2,
            "2024-01-03",
            Action.BUY,
            amount="-2000",
            units="200",
            price="10",
            fee="-0.35",
            account="IBKR-PERSONAL",
        ),
        make_row(
            3,
            "2024-01-02",
            Action.BUY,
            amount="-1004.95",
            units="100",
            price="10",
            fee="4.95",
            account="HAND-TFSA",
        ),
    ]
    signs = detect_fee_signs(rows)
    assert signs["IBKR-PERSONAL"] == D("-1")
    assert signs["HAND-TFSA"] == D("1")


def test_an_account_with_no_fees_defaults_to_positive_meaning_charged() -> None:
    """What a hand-entered `--fee 4.95` means, and moot when every fee is zero."""
    rows = [
        make_row(
            1,
            "2024-01-02",
            Action.BUY,
            amount="-1000",
            units="100",
            price="10",
            account="WS-PERSONAL",
        ),
    ]
    assert detect_fee_signs(rows) == {}
    trade = resolve_trade_cash(rows[0], FeeConvention.EXCLUDED)
    assert trade.cost == D("1000")


def test_a_negative_fee_is_a_charge_where_that_is_the_convention() -> None:
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                fee="-0.35",
            ),
            make_row(
                2,
                "2024-01-03",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                fee="-0.35",
            ),
        ],
    )
    # The commission is added to the cost base, not netted out of it.
    assert result.rows[-1].acct.acb_cad == D("2000.70")
    cash = result.cash[(Scope.ACCOUNT, "IBKR-PERSONAL", Currency.CAD)]
    assert cash.cash == D("-2000.70")
    assert cash.fees == D("0.70")


def test_a_positive_fee_on_a_negative_fee_account_is_a_rebate() -> None:
    """IBKR splits an order across fills and rebates part of the commission."""
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                fee="-0.35",
            ),
            make_row(
                2,
                "2024-01-03",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                fee="-0.35",
            ),
            make_row(
                3,
                "2024-01-04",
                Action.BUY,
                amount="-500",
                units="50",
                price="10",
                fee="0.02",
            ),
        ],
    )
    cash = result.cash[(Scope.ACCOUNT, "IBKR-PERSONAL", Currency.CAD)]
    # Two charges of 0.35 less a 0.02 rebate. Reading the magnitude would debit
    # the rebate instead of crediting it, an error of twice its value.
    assert cash.fees == D("0.68")
    assert cash.cash == D("-2500.68")
    assert result.rows[-1].acct.acb_cad == D("2500.68")


def test_a_positive_fee_is_a_charge_where_that_is_the_convention() -> None:
    """The adjustedcostbase.ca convention, and what `folio add --fee` means."""
    result = run(
        [
            make_row(
                1,
                "2024-01-02",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                fee="5",
                account="HAND-TFSA",
            ),
        ],
        types={"HAND-TFSA": AccountType.TFSA},
    )
    assert result.rows[-1].acct.acb_cad == D("1005")
    assert result.cash[(Scope.ACCOUNT, "HAND-TFSA", Currency.CAD)].fees == D("5")


def test_the_fx_commission_follows_the_same_sign_convention() -> None:
    """The IBKR FX commission is a flat USD debit, stored negative like the rest."""
    result = run(
        [
            make_row(
                1,
                "2024-04-01",
                Action.FXT,
                amount="-13624",
                units="10000",
                price="1.3624",
                fee="-2",
                ticker=None,
                account="IBKR-RRSP",
            ),
            make_row(
                2,
                "2024-04-02",
                Action.BUY,
                amount="-1000",
                units="100",
                price="10",
                fee="-0.35",
                currency="USD",
                account="IBKR-RRSP",
            ),
        ],
        types={"IBKR-RRSP": AccountType.RRSP},
    )
    usd = result.cash[(Scope.ACCOUNT, "IBKR-RRSP", Currency.USD)]
    assert usd.cash == D("10000") - D("2") - D("1000.35")
    assert result.cash[(Scope.ACCOUNT, "IBKR-RRSP", Currency.CAD)].cash == D("-13624")


def test_fee_convention_detection_survives_negative_fees() -> None:
    """The inclusion convention has to read the sign the same way cash does."""
    rows = [
        # IBKR: Amount is gross, commission charged separately and stored negative.
        make_row(
            1,
            "2024-01-02",
            Action.BUY,
            amount="-1000",
            units="100",
            price="10",
            fee="-1",
            account="IBKR-PERSONAL",
        ),
        make_row(
            2,
            "2024-01-03",
            Action.BUY,
            amount="-2000",
            units="200",
            price="10",
            fee="-1",
            account="IBKR-PERSONAL",
        ),
        # QuestTrade: Amount already contains it, also stored negative.
        make_row(
            3,
            "2024-01-02",
            Action.BUY,
            amount="-1004.95",
            units="100",
            price="10",
            fee="-4.95",
            account="QT-TFSA",
        ),
        make_row(
            4,
            "2024-01-03",
            Action.BUY,
            amount="-2004.95",
            units="200",
            price="10",
            fee="-4.95",
            account="QT-TFSA",
        ),
    ]
    detected = detect_fee_conventions(rows, detect_fee_signs(rows))
    assert detected["IBKR-PERSONAL"] is FeeConvention.EXCLUDED
    assert detected["QT-TFSA"] is FeeConvention.INCLUDED
