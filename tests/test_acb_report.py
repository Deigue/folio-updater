"""Tests for the cost base report the terminal and the sheets lay out."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from domain import Action, Column, Currency, CurrencyColumns, Grain, QuoteStatus
from engine.acb_report import AcbReport, AcbShape, Currencies, NoUsdFiguresError
from engine.frames import grain_column, ledger_frame
from engine.fx_rates import FxRates
from engine.gain_since_buy import bought_symbols
from engine.pools import FOLIO, Pool
from services.quotes_service import Quote

from .helpers.replay import FLAT_FX, make_row, run

if TYPE_CHECKING:
    from engine.types import TxnRow

NREG = Pool(Grain.TYPE, "NON_REGISTERED")
TXN_ID = str(Column.Txn.TXN_ID)


def _frame(rows: list[TxnRow], fx: FxRates = FLAT_FX) -> pd.DataFrame:
    return ledger_frame(run(rows, fx))


def _buy(
    txn_id: int,
    date: str,
    *,
    ticker: str = "AAA",
    price: str = "100",
    units: str = "10",
    currency: str = "CAD",
    settle_date: str | None = None,
    account: str = "IBKR-PERSONAL",
) -> TxnRow:
    amount = -Decimal(price) * Decimal(units)
    return make_row(
        txn_id,
        date,
        Action.BUY,
        amount=str(amount),
        units=units,
        price=price,
        currency=currency,
        ticker=ticker,
        settle_date=settle_date,
        account=account,
    )


def _sell(txn_id: int, date: str, *, ticker: str = "AAA", units: str = "10") -> TxnRow:
    return make_row(
        txn_id,
        date,
        Action.SELL,
        amount=str(Decimal(units) * 100),
        units=f"-{units}",
        price="100",
        ticker=ticker,
    )


def _usd_cash(txn_id: int) -> TxnRow:
    return make_row(
        txn_id,
        "2024-01-03",
        Action.CONTRIBUTION,
        amount="500",
        currency="USD",
        ticker=None,
    )


def _quote(price: str) -> Quote:
    return Quote(
        symbol="AAA",
        provider_symbol="AAA",
        price=Decimal(price),
        currency=Currency.CAD,
        status=QuoteStatus.OK,
    )


# -- rows ---------------------------------------------------------------------


def test_a_buildup_runs_oldest_settlement_first_and_the_ledger_in_replay_order() -> (
    None
):
    frame = _frame(
        [
            _buy(1, "2024-01-02", settle_date="2024-01-10"),
            _buy(2, "2024-01-03", settle_date="2024-01-05"),
        ],
    )

    buildup = AcbReport.build(frame, NREG, AcbShape.BUILDUP, symbol="AAA")
    ledger = AcbReport.build(frame, NREG, AcbShape.LEDGER)

    assert buildup.rows[TXN_ID].tolist() == [2, 1]
    assert ledger.rows[TXN_ID].tolist() == [1, 2]


def test_rows_are_the_pool_symbol_dates_and_income_asked_for() -> None:
    frame = _frame(
        [
            _buy(1, "2024-01-02"),
            _buy(2, "2024-02-02"),
            _buy(3, "2024-03-02"),
            _buy(4, "2024-02-02", ticker="BBB"),
            make_row(5, "2024-02-10", Action.DIVIDEND, amount="5", ticker="AAA"),
        ],
    )
    other_pool = frame.copy()
    other_pool[str(Column.Ledger.ACCT_TYPE)] = "TFSA"

    dates = ("2024-02-01", "2024-02-28")
    picked = AcbReport.build(frame, NREG, AcbShape.BUILDUP, symbol="AAA", dates=dates)
    with_income = AcbReport.build(
        frame,
        NREG,
        AcbShape.BUILDUP,
        symbol="AAA",
        dates=dates,
        income=True,
    )
    elsewhere = AcbReport.build(other_pool, NREG, AcbShape.BUILDUP, symbol="AAA")

    assert picked.rows[TXN_ID].tolist() == [2]
    assert with_income.rows[TXN_ID].tolist() == [2, 5]
    assert elsewhere.rows.empty


def test_stored_columns_the_ledger_lacks_are_joined_with_the_dropped_rows() -> None:
    frame = _frame([_buy(1, "2024-01-02")])
    txns = pd.DataFrame({TXN_ID: [1, 2], "Notes": ["kept", "dropped"]})

    report = AcbReport.build(frame, FOLIO, AcbShape.LEDGER, income=True, txns=txns)

    assert report.stored_columns == ("Notes",)
    assert report.rows[TXN_ID].tolist() == [1, 2]
    assert report.rows["Notes"].tolist() == ["kept", "dropped"]


# -- holdings -----------------------------------------------------------------


def test_holdings_list_open_symbols_first_then_closed_ones_alphabetically() -> None:
    frame = _frame(
        [
            _buy(1, "2024-01-02", ticker="AAA"),
            _sell(2, "2024-01-03", ticker="AAA"),
            _buy(3, "2024-01-02", ticker="CCC"),
            _buy(4, "2024-01-02", ticker="BBB"),
            _buy(5, "2024-01-02", ticker="DDD"),
            _sell(6, "2024-01-03", ticker="DDD"),
        ],
    )

    report = AcbReport.build(frame, FOLIO, AcbShape.SUMMARY)

    assert report.holdings["Symbol"].tolist() == ["BBB", "CCC", "AAA", "DDD"]


# -- the USD answer -----------------------------------------------------------


def test_usd_columns_apply_only_where_a_row_shown_is_in_usd() -> None:
    cad = AcbReport.build(
        _frame([_buy(1, "2024-01-02")]),
        FOLIO,
        AcbShape.BUILDUP,
        symbol="AAA",
    )
    usd = AcbReport.build(
        _frame([_buy(1, "2024-01-02", currency="USD")]),
        FOLIO,
        AcbShape.BUILDUP,
        symbol="AAA",
    )

    assert cad.currencies == Currencies(cad=True, usd=False)
    assert usd.currencies == Currencies(cad=True, usd=True)


def test_a_usd_cash_row_with_no_symbol_adds_usd_to_the_ledger_but_not_a_summary() -> (
    None
):
    frame = _frame([_buy(1, "2024-01-02"), _usd_cash(2)])

    ledger = AcbReport.build(frame, FOLIO, AcbShape.LEDGER, income=True)
    summary = AcbReport.build(frame, FOLIO, AcbShape.SUMMARY)

    assert ledger.currencies.usd
    assert not summary.currencies.usd


def test_a_summary_asks_whether_a_holding_has_a_usd_cost_base() -> None:
    # A CAD holding paying its dividend in USD has no USD cost base to show.
    frame = _frame(
        [
            _buy(1, "2024-01-02", ticker="CADCO"),
            make_row(
                2,
                "2024-01-05",
                Action.DIVIDEND,
                amount="7",
                currency="USD",
                ticker="CADCO",
            ),
        ],
    )

    summary = AcbReport.build(frame, FOLIO, AcbShape.SUMMARY, income=True)

    assert not summary.currencies.usd
    with pytest.raises(NoUsdFiguresError):
        AcbReport.build(
            frame,
            FOLIO,
            AcbShape.SUMMARY,
            currencies=CurrencyColumns.USD,
            income=True,
        )


def test_the_choice_of_columns_decides_cad_and_usd() -> None:
    frame = _frame([_buy(1, "2024-01-02", currency="USD")])

    only_cad = AcbReport.build(
        frame,
        FOLIO,
        AcbShape.LEDGER,
        currencies=CurrencyColumns.CAD,
    )
    only_usd = AcbReport.build(
        frame,
        FOLIO,
        AcbShape.LEDGER,
        currencies=CurrencyColumns.USD,
    )

    assert only_cad.currencies == Currencies(cad=True, usd=False)
    assert only_usd.currencies == Currencies(cad=False, usd=True)


def test_usd_alone_is_refused_where_nothing_is_in_usd() -> None:
    frame = _frame([_buy(1, "2024-01-02")])

    with pytest.raises(NoUsdFiguresError):
        AcbReport.build(frame, FOLIO, AcbShape.LEDGER, currencies=CurrencyColumns.USD)


def test_usd_alone_leaves_the_cad_holdings_out_of_the_holdings() -> None:
    frame = _frame(
        [
            _buy(1, "2024-01-02", ticker="CADCO"),
            make_row(
                2,
                "2024-01-05",
                Action.DIVIDEND,
                amount="7",
                currency="USD",
                ticker="CADCO",
            ),
            _buy(3, "2024-01-02", currency="USD"),
        ],
    )

    report = AcbReport.build(
        frame,
        FOLIO,
        AcbShape.SUMMARY,
        currencies=CurrencyColumns.USD,
        income=True,
    )

    assert report.holdings["Symbol"].tolist() == ["AAA"]


# -- directions ---------------------------------------------------------------


def test_directions_follow_each_currency_apart() -> None:
    fx = FxRates(("2024-01-01", "2024-02-01"), (Decimal("1.5"), Decimal("1.9")))
    frame = _frame(
        [
            _buy(1, "2024-01-02", currency="USD"),
            _buy(2, "2024-02-02", currency="USD", price="80"),
        ],
        fx,
    )

    report = AcbReport.build(frame, NREG, AcbShape.BUILDUP, symbol="AAA")

    # The USD average fell from 100 to 90 while the CAD one rose from 150 to 151.
    assert report.directions[("Avg", 2)] == 1
    assert report.directions[("Avg_USD", 2)] == -1
    assert report.directions[("Units", 2)] == 1
    assert ("Avg", 1) not in report.directions


def test_directions_are_measured_in_replay_order_not_settlement_order() -> None:
    frame = _frame(
        [
            _buy(1, "2024-01-02", price="100", settle_date="2024-01-10"),
            _buy(2, "2024-01-03", price="50", settle_date="2024-01-05"),
        ],
    )

    report = AcbReport.build(frame, NREG, AcbShape.BUILDUP, symbol="AAA")

    # Listed 2 then 1, but the replay bought 1 first, so the average fell on 2.
    assert report.rows[TXN_ID].tolist() == [2, 1]
    assert report.directions[("Avg", 2)] == -1
    assert ("Avg", 1) not in report.directions


def test_a_split_moves_units_and_average_but_not_the_cost_base() -> None:
    frame = _frame(
        [
            _buy(1, "2024-01-02"),
            make_row(2, "2024-01-03", Action.SPLIT, units="2", price="1", ticker="AAA"),
        ],
    )

    report = AcbReport.build(frame, NREG, AcbShape.BUILDUP, symbol="AAA")

    assert report.directions[("Units", 2)] == 1
    assert report.directions[("Avg", 2)] == -1
    assert {measure for measure, _ in report.directions} == {"Units", "Avg"}
    acb = report.rows[grain_column(Grain.TYPE, "ACB")].tolist()
    assert acb[0] == acb[1]


# -- flag counts --------------------------------------------------------------


def test_flag_counts_keep_the_superficial_loss_flag_but_not_for_a_summary() -> None:
    frame = _frame(
        [
            _buy(1, "2024-01-02"),
            make_row(
                2,
                "2024-01-03",
                Action.SELL,
                amount="400",
                units="-5",
                price="80",
                ticker="AAA",
            ),
            _buy(3, "2024-01-10", units="5", price="80"),
            _sell(4, "2024-03-20", units="50"),
        ],
    )

    buildup = AcbReport.build(frame, FOLIO, AcbShape.BUILDUP, symbol="AAA")
    summary = AcbReport.build(frame, FOLIO, AcbShape.SUMMARY)

    assert buildup.flag_counts["OVERSELL"] == 1
    assert buildup.flag_counts["SUPERFICIAL_LOSS_SUSPECT"] == 1
    assert summary.flag_counts["OVERSELL"] == 1
    assert "SUPERFICIAL_LOSS_SUSPECT" not in summary.flag_counts
    assert list(buildup.flag_counts) == sorted(buildup.flag_counts)


# -- pricing ------------------------------------------------------------------


def test_pricing_works_out_each_buys_gain_in_a_new_report() -> None:
    frame = _frame([_buy(1, "2024-01-02"), _buy(2, "2024-01-03", ticker="BBB")])
    report = AcbReport.build(frame, NREG, AcbShape.BUILDUP, symbol="AAA")

    priced = report.priced({"AAA": _quote("120")}, FLAT_FX)

    assert bought_symbols(report.rows) == ["AAA"]
    assert priced.rows[grain_column(Grain.TYPE, "Gain")].tolist() == [200]
    assert priced.rows[grain_column(Grain.TYPE, "GainPct")].tolist() == [0.2]
    assert grain_column(Grain.TYPE, "GainPct") not in report.rows.columns


def test_a_split_in_another_account_counts_toward_a_buys_gain() -> None:
    frame = _frame(
        [
            _buy(1, "2024-01-02"),
            _buy(2, "2024-01-02", account="IBKR-OTHER"),
            make_row(
                3,
                "2024-01-10",
                Action.SPLIT,
                units="2",
                price="1",
                ticker="AAA",
                account="IBKR-OTHER",
            ),
        ],
    )
    report = AcbReport.build(
        frame,
        Pool(Grain.ACCOUNT, "IBKR-PERSONAL"),
        AcbShape.LEDGER,
    )

    priced = report.priced({"AAA": _quote("60")}, FLAT_FX)

    # 20 units at 60 is 1,200 against the 1,000 paid.
    assert priced.rows[grain_column(Grain.ACCOUNT, "Gain")].tolist() == [200]


# -- what a program reads -----------------------------------------------------


def test_data_is_the_holdings_for_a_summary_and_every_grains_rows_otherwise() -> None:
    frame = _frame([_buy(1, "2024-01-02", currency="USD")])

    summary = AcbReport.build(frame, FOLIO, AcbShape.SUMMARY)
    rows = AcbReport.build(frame, FOLIO, AcbShape.LEDGER).priced(
        {"AAA": _quote("120")},
        FLAT_FX,
    )

    assert summary.data is summary.holdings
    assert rows.data is rows.rows
    for grain in Grain:
        assert grain_column(grain, "ACB_USD") in summary.data.columns
        assert grain_column(grain, "ACB_USD") in rows.data.columns
        assert grain_column(grain, "GainPct") in rows.data.columns


def test_every_pool_lists_the_folio_then_each_type_then_each_account() -> None:
    frame = _frame(
        [
            _buy(1, "2024-01-02", account="WS-PERSONAL"),
            _buy(2, "2024-01-02", account="IBKR-PERSONAL"),
        ],
    )

    reports = AcbReport.for_every_pool(frame)

    assert [report.pool for report in reports] == [
        FOLIO,
        NREG,
        Pool(Grain.ACCOUNT, "IBKR-PERSONAL"),
        Pool(Grain.ACCOUNT, "WS-PERSONAL"),
    ]
    assert all(
        report.currencies == Currencies(cad=True, usd=False) for report in reports
    )
